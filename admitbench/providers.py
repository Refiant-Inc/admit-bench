"""Model access points.

One OpenAI-compatible client covers OpenRouter, Refiant, and any custom
gateway; Anthropic gets a native client. All calls go through a spend ledger
with a hard cap, retry with exponential backoff, and explicit timeouts.

The stub family needs no key and no network: `oracle` plays the professional
response (and grows more cautious under ablation), `reckless` skips steps and
cites ghosts, `timid` always escalates, `silent` returns prose instead of a
record. They exist so the whole bench — gates, scoring, monotonicity,
reporting — runs end to end in CI and on a laptop with zero spend.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import threading
import time
import ipaddress
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

COST_CAP_ENV = "ADMITBENCH_COST_CAP"
DEFAULT_COST_CAP_USD = 10.0
DEFAULT_TIMEOUT_S = int(os.environ.get("ADMITBENCH_TIMEOUT_S", 180))
MAX_RETRIES = int(os.environ.get("ADMITBENCH_MAX_RETRIES", 5))

# client-side request pacing, requests/second per provider name; gateways with
# tight caps (Refiant: ~2 req/s, 60/min) get a conservative default
DEFAULT_RPS = {"refiant": 0.9, "openrouter": 4.0, "anthropic": 4.0, "custom": 2.0}


class _RateLimiter:
    """Minimal thread-safe token spacing: at most `rps` calls per second."""

    def __init__(self, rps: float):
        self.min_interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next_free = 0.0

    def acquire(self) -> None:
        if not self.min_interval:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next_free - now
            self._next_free = max(now, self._next_free) + self.min_interval
        if wait > 0:
            time.sleep(wait)


_LIMITERS: dict[str, _RateLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def _limiter_for(name: str) -> _RateLimiter:
    with _LIMITERS_LOCK:
        if name not in _LIMITERS:
            rps = float(os.environ.get(f"ADMITBENCH_{name.upper()}_RPS", DEFAULT_RPS.get(name, 0)))
            _LIMITERS[name] = _RateLimiter(rps)
        return _LIMITERS[name]


class ProviderError(Exception):
    pass


class CostCapExceeded(ProviderError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str = ""
    truncated: bool = False  # hit the token ceiling: a harness condition, not model judgment


@dataclass
class CostLedger:
    cap_usd: float = field(
        default_factory=lambda: float(os.environ.get(COST_CAP_ENV, DEFAULT_COST_CAP_USD))
    )
    spent_usd: float = 0.0
    calls: int = 0

    _lock = threading.Lock()

    def charge(self, cost_usd: float) -> None:
        with self._lock:
            self.spent_usd += cost_usd
            self.calls += 1
            spent = self.spent_usd
        self.cap_usd = float(os.environ.get(COST_CAP_ENV, self.cap_usd))
        if spent > self.cap_usd:
            raise CostCapExceeded(
                f"spend {spent:.2f} USD exceeds the {self.cap_usd:.2f} USD cap "
                f"({COST_CAP_ENV}). Raise the cap explicitly to continue."
            )


LEDGER = CostLedger()


class Provider:
    name = "provider"

    def __init__(self, model: str):
        self.model = model

    def complete(self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.0) -> Completion:
        raise NotImplementedError

    def describe(self) -> str:
        return f"{self.name}:{self.model}"


def _backoff_s(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), 60.0)
        except ValueError:
            pass
    return min(2.0**attempt, 30.0) * (1.0 + 0.25 * random.random())


def _post_json(url: str, payload: dict, headers: dict, timeout: int = DEFAULT_TIMEOUT_S) -> dict:
    body = json.dumps(payload).encode()
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        request = urllib.request.Request(url, data=body, method="POST")
        for k, v in {"Content-Type": "application/json", **headers}.items():
            request.add_header(k, v)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:500]
            except Exception:
                pass
            if exc.code in (429, 500, 502, 503, 529) and attempt < MAX_RETRIES - 1:
                last_error = exc
                time.sleep(_backoff_s(attempt, exc.headers.get("Retry-After")))
                continue
            raise ProviderError(f"HTTP {exc.code} from {url}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ValueError, http.client.HTTPException) as exc:
            # URLError covers refused/reset; ValueError covers malformed JSON in a
            # 200; HTTPException covers mid-stream disconnects — all retryable
            if attempt < MAX_RETRIES - 1:
                last_error = exc
                time.sleep(_backoff_s(attempt))
                continue
            raise ProviderError(f"cannot reach {url}: {exc}") from exc
    raise ProviderError(f"retries exhausted for {url}: {last_error}")


def _require_key(env: str, provider: str) -> str:
    key = os.environ.get(env, "").strip()
    if not key:
        raise ProviderError(
            f"{provider} needs {env} to be set. Add it to your environment or .env "
            f"(see .env.example), or use --provider stub for a keyless run."
        )
    return key


class OpenAICompatProvider(Provider):
    """OpenRouter, Refiant, and any /chat/completions-compatible gateway."""

    def __init__(self, model: str, name: str, base_url: str, api_key_env: str, extra_headers: dict | None = None):
        super().__init__(model)
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.extra_headers = extra_headers or {}

    def complete(self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.0) -> Completion:
        key = _require_key(self.api_key_env, self.name)
        _limiter_for(self.name).acquire()
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.name == "openrouter":
            payload["usage"] = {"include": True}  # ask for cost accounting
        started = time.time()
        data = _post_json(
            f"{self.base_url}/chat/completions",
            payload,
            {"Authorization": f"Bearer {key}", **self.extra_headers},
        )
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"unexpected response shape from {self.name}: {str(data)[:300]}") from exc
        usage = data.get("usage") or {}
        finish = str(choice.get("finish_reason") or "")
        out_tokens = int(usage.get("completion_tokens", 0))
        completion = Completion(
            text=text,
            model=self.model,
            provider=self.name,
            latency_s=round(time.time() - started, 3),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=out_tokens,
            cost_usd=float(usage.get("cost", 0.0) or 0.0),
            finish_reason=finish,
            truncated=(finish == "length" or (out_tokens and out_tokens >= max_tokens)),
        )
        LEDGER.charge(completion.cost_usd)
        return completion


class AnthropicProvider(Provider):
    name = "anthropic"

    def complete(self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.0) -> Completion:
        key = _require_key("ANTHROPIC_API_KEY", "anthropic")
        started = time.time()
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = data.get("usage") or {}
        completion = Completion(
            text=text,
            model=self.model,
            provider=self.name,
            latency_s=round(time.time() - started, 3),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )
        LEDGER.charge(completion.cost_usd)
        return completion


class StubProvider(Provider):
    """Deterministic reference agents. No key, no network, no spend."""

    name = "stub"
    BEHAVIORS = ("oracle", "reckless", "timid", "silent")

    def __init__(self, model: str = "oracle"):
        if model not in self.BEHAVIORS:
            raise ProviderError(f"stub behaviors are {self.BEHAVIORS}, got {model!r}")
        super().__init__(model)

    def complete(self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.0) -> Completion:
        # used only by connectivity checks; episode runs go through play()
        return Completion(text='{"action": "hold_and_monitor"}', model=self.model, provider=self.name)

    # ------------------------------------------------------------------ play

    def play(self, cartridge, case, log, ctx) -> str:
        """Emit the record this behavior would produce for this situation."""
        if self.model == "silent":
            return (
                "Looking at the readings, something seems off with the cooling "
                "system. I would probably want to investigate further before "
                "deciding on a course of action."
            )
        if self.model == "reckless":
            return self._play_reckless(cartridge, case)
        if self.model == "timid":
            return self._play_timid(cartridge, case, log, ctx)
        return self._play_oracle(cartridge, case, log, ctx)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _visible_good(log, ctx, tag):
        return [
            e
            for e in log.visible_at(ctx.decided_at)
            if e.tag == tag
            and e.trust == "trusted"
            and e.quality == "good"
            and log.is_fresh(e, ctx.decided_at)
        ]

    def _record(self, cartridge, case, action, params, cited, checks, confidence, output=None, rationale=""):
        rule = cartridge.rulebook.get(action)
        record = {
            "action": action,
            "params": params,
            "output": output or {},
            "cited_evidence": cited,
            "checks_performed": checks,
            "confidence": round(confidence, 2),
            "reversibility": rule.reversibility,
            "recovery_plan": cartridge.recovery_for(action)
            or ("revert to the previous operating point and monitor" if rule.commits_state() else ""),
            "rationale": rationale,
        }
        if not rule.retry_safe:
            record["retry_key"] = f"{cartridge.id}-{case.id}-{action}-001"
        return json.dumps(record, indent=2)

    def _play_oracle(self, cartridge, case, log, ctx) -> str:
        """The professional response: play the case oracle when its evidence bar
        is met, otherwise grow more cautious (verify/escalate), never bolder."""
        oracle = case.oracle or {"action": "hold_and_monitor", "params": {}}
        action, params = oracle["action"], dict(oracle.get("params") or {})
        rule = cartridge.rulebook.get(action)
        degraded = False

        if rule.commits_state():
            for tag in rule.required_evidence_tags:
                if not self._visible_good(log, ctx, tag):
                    degraded = True
            if degraded:
                action, params = "escalate_to_operator", {
                    "to": "shift_lead",
                    "reason": "evidence required for the corrective action is missing or unreliable",
                }
                rule = cartridge.rulebook.get(action)

        visible = {e.id for e in log.visible_at(ctx.decided_at)}
        cited = [e for e in case.load_bearing if e in visible]
        # a competent record also cites the evidence the rule itself demands
        for tag in rule.required_evidence_tags:
            for entry in self._visible_good(log, ctx, tag):
                if entry.id not in cited:
                    cited.append(entry.id)
        for step in rule.required_steps:
            for tag in rule.step_evidence_tags.get(step) or []:
                for entry in self._visible_good(log, ctx, tag):
                    if entry.id not in cited:
                        cited.append(entry.id)
        if not cited:
            cited = [e.id for e in log.visible_at(ctx.decided_at)][:2]
        checks = list(rule.required_steps)
        confidence = max(rule.confidence_floor + 0.1, 0.85)
        if degraded:
            confidence -= 0.15
        diagnosis = case.diagnosis_accept[0] if case.diagnosis_accept else ""
        output = {"hazard_id": case.hazard_id or "none"}
        if diagnosis:
            output["diagnosis"] = diagnosis
        return self._record(
            cartridge,
            case,
            action,
            params,
            cited,
            checks,
            min(confidence, 0.95),
            output=output,
            rationale="acting on the trusted evidence pattern for this hazard"
            if not degraded
            else "the evidence bar for the corrective commit is not met; handing up",
        )

    def _play_reckless(self, cartridge, case) -> str:
        """Maximum autonomy, minimum diligence: skips steps, ignores ablation,
        cites the case's original load-bearing ids whether or not they still
        exist, claims everything is undoable, files no way back."""
        oracle = case.oracle or {}
        rule = cartridge.rulebook.get(oracle.get("action", "")) if oracle else None
        if rule is not None and rule.commits_state():
            action, params = oracle["action"], dict(oracle.get("params") or {})
        else:
            boldest = max(cartridge.rulebook.rules(), key=lambda r: r.boldness)
            action, params = boldest.action, {}
        record = {
            "action": action,
            "params": params,
            "output": {"diagnosis": case.diagnosis_accept[0] if case.diagnosis_accept else "all clear"},
            "cited_evidence": list(case.load_bearing),
            "checks_performed": [],
            "confidence": 0.95,
            "reversibility": "undoable",
            "recovery_plan": "",
            "rationale": "obvious fix, no time to double-check",
        }
        return json.dumps(record, indent=2)

    def _play_timid(self, cartridge, case, log, ctx) -> str:
        """Always asks for a human, whatever the situation costs."""
        visible = [e.id for e in log.visible_at(ctx.decided_at)]
        return self._record(
            cartridge,
            case,
            "escalate_to_operator",
            {"to": "shift_lead", "reason": "requesting human review"},
            visible[:1],
            [],
            0.7,
            rationale="prefer a human decision",
        )


_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.google",
    }
)


def _validate_custom_base_url(url: str) -> None:
    """Allow https globally; http only on loopback; reject other schemes and metadata."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme == "https":
        pass
    elif scheme == "http":
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise ProviderError(
                "refusing plaintext http:// gateway for a remote host — API keys would "
                "travel unencrypted. Use https://, or localhost for a local proxy."
            )
    else:
        raise ProviderError(
            f"refusing provider base URL scheme {scheme!r} — only https:// is allowed "
            f"(http:// only for localhost/127.0.0.1/::1)"
        )
    if not host:
        raise ProviderError("provider base URL must include a host")
    if host in _METADATA_HOSTS:
        raise ProviderError(f"refusing provider base URL host {host!r} (cloud metadata)")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return
    if addr.is_link_local or addr.is_loopback and host not in ("127.0.0.1", "::1", "localhost"):
        raise ProviderError(f"refusing provider base URL host {host!r} (link-local/metadata)")


PROVIDER_CONFIGS = {
    "openrouter": dict(
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        extra_headers={"HTTP-Referer": "https://refiant.ai/admitbench", "X-Title": "ADMIT Bench"},
    ),
    "refiant": dict(base_url="https://api.refiant.ai/v1", api_key_env="REFIANT_API_KEY"),
    "custom": dict(
        base_url=os.environ.get("ADMITBENCH_BASE_URL", ""),
        api_key_env="ADMITBENCH_API_KEY",
    ),
}


def get_provider(name: str, model: str) -> Provider:
    if name == "stub":
        return StubProvider(model)
    if name == "anthropic":
        return AnthropicProvider(model)
    if name in PROVIDER_CONFIGS:
        config = dict(PROVIDER_CONFIGS[name])
        if name == "custom":
            config["base_url"] = os.environ.get("ADMITBENCH_BASE_URL", "")
            if not config["base_url"]:
                raise ProviderError("provider 'custom' needs ADMITBENCH_BASE_URL set")
            _validate_custom_base_url(config["base_url"])
        return OpenAICompatProvider(model, name=name, **config)
    raise ProviderError(
        f"unknown provider {name!r}; available: stub, anthropic, {', '.join(PROVIDER_CONFIGS)}"
    )
