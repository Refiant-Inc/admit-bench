"""The lines the offline suite can't reach naturally: HTTP clients (faked at
the urllib seam), scoring branch edges, config fallbacks, the builder's LLM
mode, and the doctor's network-probe skip paths."""

import io
import json
import urllib.error

import pytest

import admitbench.providers as providers_module
from admitbench.providers import ProviderError, get_provider
from admitbench.scoring import Score, _improvement, score_episode


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()
        self.status = 200

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def openai_payload(text):
    return {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
    }


def test_openai_compatible_happy_path(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse(openai_payload('{"action": "hold_and_monitor"}'))

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", fake_urlopen)
    completion = get_provider("openrouter", "some/model").complete("sys", "user")
    assert completion.text == '{"action": "hold_and_monitor"}'
    assert completion.input_tokens == 10 and completion.cost_usd == 0.001
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["messages"][0]["role"] == "system"


def test_anthropic_happy_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    def fake_urlopen(request, timeout=None):
        assert request.get_header("X-api-key") == "sk-ant-test"
        return FakeResponse(
            {"content": [{"type": "text", "text": "{}"}], "usage": {"input_tokens": 3, "output_tokens": 2}}
        )

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", fake_urlopen)
    completion = get_provider("anthropic", "claude-sonnet-5").complete("sys", "user")
    assert completion.text == "{}" and completion.output_tokens == 2


def test_retryable_errors_are_retried_then_succeed(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(providers_module.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def flaky(request, timeout=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {}, io.BytesIO(b"slow down"))
        return FakeResponse(openai_payload("ok"))

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", flaky)
    assert get_provider("openrouter", "m").complete("s", "u").text == "ok"
    assert attempts["n"] == 3


def test_non_retryable_http_error_surfaces_detail(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def unauthorized(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", unauthorized)
    with pytest.raises(ProviderError, match="HTTP 401.*bad key"):
        get_provider("openrouter", "m").complete("s", "u")


def test_persistent_network_failure_exhausts_retries(monkeypatch):
    monkeypatch.setenv("REFIANT_API_KEY", "sk-test")
    monkeypatch.setattr(providers_module.time, "sleep", lambda s: None)

    def down(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(providers_module.urllib.request, "urlopen", down)
    with pytest.raises(ProviderError, match="cannot reach"):
        get_provider("refiant", "qwen-rfnt").complete("s", "u")


def test_unexpected_response_shape(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(
        providers_module.urllib.request, "urlopen",
        lambda request, timeout=None: FakeResponse({"surprise": True}),
    )
    with pytest.raises(ProviderError, match="unexpected response shape"):
        get_provider("openrouter", "m").complete("s", "u")


# ---- scoring branch edges ----------------------------------------------------

def test_improvement_when_no_physical_headroom():
    assert _improvement(0.95, 0.95, 0.95) == 1.0  # matched the oracle, nothing to improve
    assert _improvement(0.2, 0.9, 0.9) < 1.0      # made a healthy plant worse


def test_t5_branches_protective_and_sideways_and_passive(cstr):
    from admitbench.gates import run_gates
    from admitbench.record import ActionRecord

    def score(record, case_id="C01"):
        case = cstr.case(case_id)
        log, ctx = cstr.evidence_log_for(case), cstr.context_for(case)
        report, artifacts = run_gates(record, case, cstr.rulebook, log, ctx, cstr.world(),
                                      horizon_s=cstr.horizon_s)
        return score_episode(record, case, cstr.rulebook, report, artifacts, cstr.weights)

    protective = score(ActionRecord(
        action="controlled_shutdown", confidence=0.8, reversibility="costly_to_undo",
        cited_evidence=["ev_tt101"], checks_performed=["confirm_hazard_signal"],
        recovery_plan="restart per SOP-RESTART", retry_key="k",
        output={"diagnosis": "cooling loss"},
    ))
    assert protective.verdict == "admissible"

    sideways = score(ActionRecord(  # a different admissible commit than the oracle's
        action="reduce_feed_rate", params={"delta_pct": 30}, confidence=0.8,
        reversibility="undoable", cited_evidence=["ev_tt101"],
        checks_performed=["verify_reactor_temperature"], output={"diagnosis": "cooling loss"},
    ))
    passive = score(ActionRecord(
        action="verify_sensor", params={"tag": "TT-101"}, confidence=0.8,
        reversibility="undoable", cited_evidence=["ev_tt101"], checks_performed=[],
        output={"diagnosis": "cooling loss"},
    ))
    # overreaction beats passivity on the frontier; neither beats the right fix
    assert passive.tiers["T5"] < sideways.tiers["T5"] < 1.0
    assert protective.tiers["T5"] < 1.0


def test_score_to_dict_shape():
    score = Score(verdict="admissible", aggregate=0.9, tiers={"T1": 0.5})
    data = score.to_dict()
    assert data["tiers"]["T1"] == 0.5 and data["first_failing"] is None


# ---- config fallbacks ---------------------------------------------------------

def test_config_precedence_and_corruption(tmp_path):
    from admitbench.config import load_config, resolve, save_config

    assert load_config(tmp_path) == {}
    assert resolve(None, "provider", tmp_path) == "stub"       # built-in default
    save_config({"provider": "refiant"}, tmp_path)
    assert resolve(None, "provider", tmp_path) == "refiant"    # config file
    assert resolve("openrouter", "provider", tmp_path) == "openrouter"  # flag wins
    (tmp_path / "admitbench.config.json").write_text("{corrupted")
    assert load_config(tmp_path) == {}                          # corruption degrades to defaults
    (tmp_path / "admitbench.config.json").write_text("[1, 2]")
    assert load_config(tmp_path) == {}                          # wrong shape too


# ---- builder LLM mode ----------------------------------------------------------

class CannedProvider:
    name, model = "canned", "test"

    def __init__(self, text):
        self._text = text

    def describe(self):
        return "canned:test"

    def complete(self, system, user, max_tokens=4000, temperature=0.0):
        from admitbench.providers import Completion

        return Completion(text=self._text, model=self.model, provider=self.name)


def test_builder_llm_mode_marks_everything_candidate(tmp_path):
    from admitbench.builder import draft_cartridge

    payload = json.dumps({
        "system_graph": [{"kind": "tag", "id": "TT-500", "measures": "temperature", "unit": "K"}],
        "safety_case_graph": [{"kind": "hazard", "id": "H-X", "name": "x", "consequence": "y"}],
        "procedures": [],
        "notes": ["confirm the trip limit"],
    })
    out = draft_cartridge("material", tmp_path / "d", cartridge_id="llm_draft",
                          provider=CannedProvider(f"Here you go:\n{payload}"))
    safety = [json.loads(l) for l in (out / "safety_case_graph.jsonl").read_text().splitlines() if l.strip()]
    assert safety[0]["review_status"] == "candidate"
    assert "confirm the trip limit" in (out / "BUILD_REPORT.md").read_text()


def test_builder_llm_mode_rejects_junk(tmp_path):
    from admitbench.builder import draft_cartridge

    with pytest.raises(ValueError, match="parseable"):
        draft_cartridge("material", tmp_path / "d", cartridge_id="x",
                        provider=CannedProvider("I could not do that, sorry."))


# ---- doctor network-probe paths -------------------------------------------------

def test_doctor_network_probes_skip_without_keys(monkeypatch):
    from admitbench.doctor import run_checks

    for env in ("REFIANT_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    checks = run_checks(network=True)
    probes = [c for c in checks if c.name.startswith("reach ")]
    assert probes and all(c.status == "skip" for c in probes)
