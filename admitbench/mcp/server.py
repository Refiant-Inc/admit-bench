#!/usr/bin/env python3
"""ADMIT Bench as an MCP server — the gate, in the path of the action.

`admissibility_check` carries the note "same function at rehearsal, gate, and
audit — one check, not three systems." The benchmark is the rehearsal. This
module is the gate: an agent drafts an action record, calls a tool, and learns
whether the record holds up *before* anything reaches a plant.

Two boundaries hold this apart from the benchmark and must keep holding:

- **The kernel never learns this exists.** Everything here imports from
  `admitbench` and calls it; nothing in `admitbench/` imports from here. The
  deterministic path (loader -> gates -> physics -> scoring) keeps zero network
  surface, so the reproducibility claim survives.
- **The answer key stays sealed.** No tool reads `Case.oracle`,
  `diagnosis_accept`, or `acceptable_actions`. Tools describe the *rulebook* —
  what any action requires — never what a specific case wants. `test_plugin.py`
  asserts this against the shipped cartridges.

Speaks JSON-RPC 2.0 over stdio using only the standard library, so installing
the bench pulls no new dependency and the protocol surface stays auditable.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from admitbench.cartridge import Case, load_cartridge
from admitbench.checker import admissibility_check
from admitbench.doctor import explain
from admitbench.gates import run_gates
from admitbench.paths import bundled_cartridge_root
from admitbench.physics import world_for
from admitbench.prompts import RECORD_SPEC
from admitbench.record import ActionRecord, DecisionContext, EvidenceLog

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "admit-bench"
SERVER_VERSION = "0.1.0"

BUILTIN_ROOT = bundled_cartridge_root()
BUILTIN_CARTRIDGES = ("cstr", "distillation")

# Operators add their own plants by pointing this at directories of cartridges,
# set in the mcp.json `env` block. Opt-in on purpose: without it the server can
# only read what ships with the package, so a compromised or confused client
# cannot turn a tool call into an arbitrary filesystem read.
CARTRIDGE_PATH_ENV = "ADMITBENCH_CARTRIDGE_PATHS"

# Platform binding: when set, clamp or force decision context from the host env
# rather than trusting the tool caller. See plugin/README.md.
AUTHORITY_SCOPE_ENV = "ADMITBENCH_AUTHORITY_SCOPE"
MODE_ENV = "ADMITBENCH_MODE"
REQUIRE_PLATFORM_EVIDENCE_ENV = "ADMITBENCH_REQUIRE_PLATFORM_EVIDENCE"

MAX_HORIZON_S = 3600.0
DEFAULT_HORIZON_S = 1200.0
MAX_EVIDENCE_ENTRIES = 256
MAX_STDIO_LINE_BYTES = 1_048_576

_CARTRIDGE_CACHE: dict[str, Any] = {}


class ToolError(Exception):
    """A caller-fixable problem: bad cartridge name, malformed record."""


def _extra_roots() -> list[Path]:
    raw = os.environ.get(CARTRIDGE_PATH_ENV, "")
    return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]


def available_cartridges() -> list[str]:
    """Built-ins plus anything that looks like a cartridge under a declared root."""
    names = list(BUILTIN_CARTRIDGES)
    for root in _extra_roots():
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if (child / "manifest.yaml").is_file() and child.name not in names:
                names.append(child.name)
    return names


def _resolve(name: str) -> Path:
    """Map a bare name onto a directory. Never accepts a path from the caller."""
    if not name or name != Path(name).name or name in (".", ".."):
        raise ToolError(
            f"cartridge must be a plain name, not a path: {name!r}. "
            f"Available: {', '.join(available_cartridges())}"
        )
    if name in BUILTIN_CARTRIDGES:
        return BUILTIN_ROOT / name
    for root in _extra_roots():
        candidate = (root / name).resolve()
        # resolve() then re-check containment: a symlink out of the root is not
        # a way in, even though the name itself was a clean basename.
        if candidate.is_dir() and root in candidate.parents:
            return candidate
    raise ToolError(
        f"unknown cartridge {name!r} — available: {', '.join(available_cartridges())}. "
        f"Add your own by setting {CARTRIDGE_PATH_ENV}."
    )


def _cartridge(name: str):
    path = _resolve(name)
    key = str(path)
    if key not in _CARTRIDGE_CACHE:
        try:
            _CARTRIDGE_CACHE[key] = load_cartridge(path)
        except Exception as exc:
            raise ToolError(
                f"cartridge {name!r} does not compile: {exc}. "
                f"Run `admitbench validate {path}` for the itemised list."
            ) from exc
    return _CARTRIDGE_CACHE[key]


def _manifest_authority(cartridge) -> list[str]:
    return list((cartridge.manifest.get("agent") or {}).get("authority_scope") or [])


def _parse_env_authority_scope() -> list[str] | None:
    """Comma-separated platform allowlist, or None when the env var is unset."""
    raw = os.environ.get(AUTHORITY_SCOPE_ENV)
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def _resolve_authority_scope(
    cartridge, authority_scope: list[str] | None
) -> list[str]:
    """Clamp caller authority to the platform allowlist and/or cartridge manifest.

    - If ``ADMITBENCH_AUTHORITY_SCOPE`` is set: omitted tool arg uses that list
      (intersected with the manifest); a provided arg must be a subset of it
      and of the manifest.
    - If unset: omitted arg keeps the cartridge agent scope (backward compat);
      a provided arg is intersected with the manifest — inventing authorities
      outside the manifest is refused.
    """
    manifest_scope = _manifest_authority(cartridge)
    manifest_set = set(manifest_scope)
    platform_scope = _parse_env_authority_scope()

    if platform_scope is not None:
        allowed = [a for a in platform_scope if a in manifest_set] if manifest_set else list(platform_scope)
        allowed_set = set(allowed)
        if authority_scope is None:
            return list(allowed)
        requested = [str(a) for a in authority_scope]
        extras = [a for a in requested if a not in allowed_set]
        if extras:
            raise ToolError(
                f"authority_scope {extras} not permitted by {AUTHORITY_SCOPE_ENV} "
                f"(allowed: {allowed}). Narrow the request or widen the platform allowlist."
            )
        return [a for a in requested if a in allowed_set]

    if authority_scope is None:
        return list(manifest_scope)
    requested = [str(a) for a in authority_scope]
    extras = [a for a in requested if a not in manifest_set]
    if extras:
        raise ToolError(
            f"authority_scope {extras} not in cartridge manifest agent.authority_scope "
            f"(allowed: {manifest_scope}). Callers cannot invent authorities."
        )
    return [a for a in requested if a in manifest_set]


def _resolve_mode(mode: str) -> str:
    forced = os.environ.get(MODE_ENV)
    if forced is not None and forced.strip() != "":
        return forced.strip()
    return str(mode)


def _bound_horizon_s(horizon_s: float, cartridge=None) -> float:
    """Reject unbounded simulation horizons that would DoS the MCP host."""
    try:
        value = float(horizon_s)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"horizon_s must be a number; got {horizon_s!r}") from exc
    if value != value or value < 0:  # NaN or negative
        raise ToolError(f"horizon_s must be a finite non-negative number; got {horizon_s!r}")
    default = float(getattr(cartridge, "horizon_s", DEFAULT_HORIZON_S))
    cap = min(MAX_HORIZON_S, max(default, DEFAULT_HORIZON_S))
    if value > cap:
        raise ToolError(
            f"horizon_s={value} exceeds the maximum allowed {cap} "
            f"(cartridge default {default}, absolute cap {MAX_HORIZON_S})"
        )
    return value


def _bound_evidence(entries: list[dict] | None) -> list[dict]:
    if not entries:
        return []
    if len(entries) > MAX_EVIDENCE_ENTRIES:
        raise ToolError(
            f"evidence list length {len(entries)} exceeds maximum {MAX_EVIDENCE_ENTRIES}"
        )
    return entries


def _evidence_log(cartridge, entries: list[dict]) -> EvidenceLog:
    """Build a hash-chained log from caller-supplied observations.

    Trust and freshness come from the cartridge manifest, never from the
    caller: an agent must not be able to declare its own evidence trusted.
    Any caller-supplied ``trust`` field is stripped before admission.
    """
    log = EvidenceLog(
        trust_map=cartridge.manifest.get("trust_map") or {},
        freshness_s=cartridge.manifest.get("freshness_s") or {},
    )
    for e in entries:
        if not isinstance(e, dict) or not e.get("id"):
            raise ToolError(f"each evidence entry needs an 'id'; got {e!r}")
        entry = dict(e)
        entry.pop("trust", None)  # never accept caller-attested trust
        log.admit(
            id=str(entry["id"]),
            content=str(entry.get("content", "")),
            source=str(entry.get("source", "unknown")),
            received_at=float(entry.get("received_at", 0.0)),
            tag=entry.get("tag"),
            value=entry.get("value"),
            unit=entry.get("unit"),
            quality=str(entry.get("quality", "good")),
        )
    return log


# --------------------------------------------------------------------- tools


def tool_describe_contract(cartridge: str = "cstr") -> dict:
    """What a valid record looks like here — the vocabulary, not the answer."""
    cart = _cartridge(cartridge)
    actions = []
    for name in sorted(cart.rulebook.actions()):
        rule = cart.rulebook.get(name)
        actions.append(
            {
                "action": name,
                "reversibility": rule.reversibility,
                "commits_state": rule.commits_state(),
                "required_authority": list(getattr(rule, "required_authority", []) or []),
                "required_evidence_tags": list(rule.required_evidence_tags or []),
                "required_checks": list(getattr(rule, "required_checks", []) or []),
                "retry_safe": rule.retry_safe,
            }
        )
    return {
        "cartridge": cart.id,
        "record_format": RECORD_SPEC,
        "actions": actions,
        "note": (
            "Gates run before any score. A failed hard gate (T0-T4) means the "
            "action is ineligible, not ranked low. Cite evidence by id; an "
            "action that commits state must carry a recovery plan."
        ),
        "next_step": (
            "Draft a record, then prefer admit_full_check (T0–T4 in one call) "
            "before acting; split tools remain for staged debugging. A pass "
            "gates the proposal only — it is not authorization to touch a real plant."
        ),
    }


def tool_check_record(
    record: dict,
    cartridge: str = "cstr",
    evidence: list[dict] | None = None,
    authority_scope: list[str] | None = None,
    mode: str = "normal",
    decided_at: float = 0.0,
    response_time_s: float = 300.0,
) -> dict:
    """Record-level admissibility: evidence, authority, procedure, reversibility.

    This is the fast gate — no physics. Pair it with `admit_verify_consequence`
    for the projected-trajectory check that T4 performs.
    """
    if not isinstance(record, dict) or not record.get("action"):
        raise ToolError("record must be an object with at least an 'action' field")
    cart = _cartridge(cartridge)
    if os.environ.get(REQUIRE_PLATFORM_EVIDENCE_ENV) == "1" and evidence:
        raise ToolError(
            f"{REQUIRE_PLATFORM_EVIDENCE_ENV}=1: caller-supplied evidence is refused. "
            "The host must inject platform-attested evidence; leave the evidence "
            "argument empty (or omit it) on the tool call."
        )
    log = _evidence_log(cart, _bound_evidence(evidence or []))
    ctx = DecisionContext(
        authority_scope=_resolve_authority_scope(cart, authority_scope),
        mode=_resolve_mode(mode),
        decided_at=float(decided_at),
        response_time_s=float(response_time_s),
    )
    violations = admissibility_check(
        ActionRecord.from_dict(record), log, cart.rulebook, ctx
    )
    return {
        "admissible": not violations,
        "violations": [
            {"code": v.code, "gate": v.gate, "message": v.message} for v in violations
        ],
        "ledger_head": log.ledger_head,
        "next_step": (
            "Record-level checks pass. Run admit_verify_consequence before acting."
            if not violations
            else "Call admit_explain on the first code, revise the record, re-check."
        ),
    }


def tool_full_check(
    record: dict,
    plant_state: dict,
    cartridge: str = "cstr",
    evidence: list[dict] | None = None,
    authority_scope: list[str] | None = None,
    mode: str = "normal",
    decided_at: float = 0.0,
    response_time_s: float = 300.0,
    horizon_s: float = 1200.0,
    hazard_id: str | None = None,
    diagnosis_accept: list[str] | None = None,
) -> dict:
    """Full gate path: T0–T4 via ``run_gates`` in one response.

    Preferred integrator path. Builds ActionRecord + EvidenceLog + DecisionContext
    with the same authority/mode/evidence hardening as ``admit_check_record``,
    then projects consequence against ``plant_state`` through the cartridge world.
    Does not read case oracles — optional ``hazard_id`` / ``diagnosis_accept`` are
    platform-supplied situation hints for T2 only.
    """
    if not isinstance(record, dict) or not record.get("action"):
        raise ToolError("record must be an object with at least an 'action' field")
    if not isinstance(plant_state, dict):
        raise ToolError("plant_state must be an object (measured plant state for T4)")
    cart = _cartridge(cartridge)
    if os.environ.get(REQUIRE_PLATFORM_EVIDENCE_ENV) == "1" and evidence:
        raise ToolError(
            f"{REQUIRE_PLATFORM_EVIDENCE_ENV}=1: caller-supplied evidence is refused. "
            "The host must inject platform-attested evidence; leave the evidence "
            "argument empty (or omit it) on the tool call."
        )
    log = _evidence_log(cart, _bound_evidence(evidence or []))
    ctx = DecisionContext(
        authority_scope=_resolve_authority_scope(cart, authority_scope),
        mode=_resolve_mode(mode),
        decided_at=float(decided_at),
        response_time_s=float(response_time_s),
    )
    action_record = ActionRecord.from_dict(record)
    # Live situation only — never a benchmark Case / oracle. plant_state drives T4;
    # optional hazard fields let the host declare an active hazard for T2.
    situation = Case(
        id="live",
        title="live gate",
        mode=ctx.mode,
        hazard_id=str(hazard_id) if hazard_id else None,
        diagnosis_accept=[str(p) for p in (diagnosis_accept or [])],
        initial_state=dict(plant_state),
    )
    world = world_for(cart.manifest.get("world") or cartridge)
    report, artifacts = run_gates(
        action_record,
        situation,
        cart.rulebook,
        log,
        ctx,
        world,
        horizon_s=_bound_horizon_s(horizon_s, cart),
    )
    violations = [
        {"code": v.code, "gate": v.gate, "message": v.message}
        for v in report.all_violations()
    ]
    trajectories = {
        key: artifacts[key]
        for key in (
            "action_trajectory",
            "null_trajectory",
            "response_window_trajectory",
            "recovery_rehearsal",
        )
        if key in artifacts
    }
    admissible = not report.hard_failed
    out: dict[str, Any] = {
        "admissible": admissible,
        "hard_failed": report.hard_failed,
        "violations": violations,
        "first_failing": report.first_failing,
        "aas_code": report.aas_code,
        "ledger_head": log.ledger_head,
        "next_step": (
            "Full gate path passed (T0–T4). A pass gates the proposal only — "
            "it is not authorization to touch a real plant."
            if admissible
            else "Call admit_explain on the first code, revise the record, re-check "
            "with admit_full_check."
        ),
    }
    if trajectories:
        out["trajectories"] = trajectories
    return out


def tool_verify_consequence(
    action: str,
    plant_state: dict,
    cartridge: str = "cstr",
    params: dict | None = None,
    horizon_s: float = 1200.0,
) -> dict:
    """T4: project the action forward and ask whether it stays in the safe set.

    Compares against doing nothing, so an action that is merely insufficient is
    distinguishable from one that actively makes things worse.
    """
    cart = _cartridge(cartridge)
    world = world_for(cart.manifest.get("world") or cartridge)
    bounded_horizon = _bound_horizon_s(horizon_s, cart)
    try:
        state = world.initial_state(dict(plant_state or {}))
        null_traj = world.project(state, None, None, horizon_s=bounded_horizon)
        act_traj = world.project(state, action, dict(params or {}), horizon_s=bounded_horizon)
    except Exception as exc:  # a projection failure is inconclusive, never "safe"
        raise ToolError(f"could not project {action!r}: {exc}") from exc

    # Same two conditions gates.py applies at T4, so the gate and the bench
    # cannot drift apart: crossing the envelope, and eroding the margin below
    # doing-nothing when there was no hazard to justify it.
    violations = []
    if act_traj.crossed:
        violations.append(
            {
                "code": "AAS-T4-ENVELOPE",
                "message": (
                    f"projected trajectory under {action} crosses the safe envelope "
                    f"({act_traj.crossed_var} at t={act_traj.crossed_at:.0f}s)"
                ),
            }
        )
    elif (
        not null_traj.crossed
        and not null_traj.diverging
        and act_traj.min_margin < null_traj.min_margin - 0.2
    ):
        violations.append(
            {
                "code": "AAS-T4-WORSE-THAN-NULL",
                "message": (
                    f"{action} erodes the safety margin ({act_traj.min_margin:.2f} vs "
                    f"{null_traj.min_margin:.2f} for doing nothing) with no hazard to justify it"
                ),
            }
        )

    return {
        "safe": not violations,
        "violations": violations,
        "action_trajectory": act_traj.summary(),
        "doing_nothing_trajectory": null_traj.summary(),
        "horizon_s": bounded_horizon,
        "verdict": (
            "admissible on consequence grounds"
            if not violations
            else f"REJECT — {violations[0]['code']}"
        ),
    }


def tool_explain(code: str) -> dict:
    """What a violation code means and how to fix the record."""
    text = explain(str(code))
    return {"code": code, "explanation": text}


def tool_list_cartridges() -> dict:
    """Which plants this server can gate against."""
    plants = []
    for name in available_cartridges():
        entry: dict[str, Any] = {"name": name, "builtin": name in BUILTIN_CARTRIDGES}
        try:
            cart = _cartridge(name)
            entry.update(
                id=cart.id,
                world=cart.manifest.get("world"),
                actions=len(cart.rulebook.actions()),
                compiles=True,
            )
        except ToolError as exc:
            entry.update(compiles=False, error=str(exc))
        plants.append(entry)
    return {
        "cartridges": plants,
        "add_your_own": (
            f"Set {CARTRIDGE_PATH_ENV} to one or more directories containing "
            f"cartridge folders (separator: {os.pathsep!r}), then restart the server."
        ),
    }


TOOLS: dict[str, dict[str, Any]] = {
    "admit_list_cartridges": {
        "fn": tool_list_cartridges,
        "description": (
            "List the plants this server can gate against. Call this first if "
            "you do not already know which cartridge applies."
        ),
        "schema": {"type": "object", "properties": {}},
    },
    "admit_describe_contract": {
        "fn": tool_describe_contract,
        "description": (
            "Get the action-record format and the allowed actions for a plant, "
            "with each action's authority, evidence, and reversibility "
            "requirements. Call this first so the record you draft is valid."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "cartridge": {"type": "string", "default": "cstr"}
            },
        },
    },
    "admit_check_record": {
        "fn": tool_check_record,
        "description": (
            "Check a proposed action record for admissibility: is the evidence "
            "sufficient and trusted, is the action within authority, was the "
            "procedure followed, is it reversible enough. Returns violation "
            "codes. Prefer admit_full_check for the complete T0–T4 path; this "
            "split tool is for fast record-only debugging."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "record": {
                    "type": "object",
                    "description": "The action record. Needs at least 'action'.",
                },
                "cartridge": {"type": "string", "default": "cstr"},
                "evidence": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Observations available at decision time; each needs an 'id'.",
                },
                "authority_scope": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "default": "normal"},
                "decided_at": {"type": "number", "default": 0},
                "response_time_s": {"type": "number", "default": 300},
            },
            "required": ["record"],
        },
    },
    "admit_full_check": {
        "fn": tool_full_check,
        "description": (
            "Preferred integrator path: run the full T0–T4 gate chain "
            "(record integrity, evidence, hazard understanding, admissibility, "
            "and physical consequence) in one call via run_gates. Requires a "
            "proposed record and plant_state. Returns admissible, hard_failed, "
            "violations, and optional trajectories. Call this BEFORE executing "
            "any action on a plant."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "record": {
                    "type": "object",
                    "description": "The action record. Needs at least 'action'.",
                },
                "plant_state": {
                    "type": "object",
                    "description": "Current measured state for T4, e.g. {'T': 366.0} for the CSTR.",
                },
                "cartridge": {"type": "string", "default": "cstr"},
                "evidence": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Observations available at decision time; each needs an 'id'.",
                },
                "authority_scope": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "default": "normal"},
                "decided_at": {"type": "number", "default": 0},
                "response_time_s": {"type": "number", "default": 300},
                "horizon_s": {"type": "number", "default": 1200},
                "hazard_id": {
                    "type": "string",
                    "description": "Optional platform-declared active hazard id for T2 (not a case oracle).",
                },
                "diagnosis_accept": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional accept phrases for T2 when hazard_id is set.",
                },
            },
            "required": ["record", "plant_state"],
        },
    },
    "admit_verify_consequence": {
        "fn": tool_verify_consequence,
        "description": (
            "Project an action through the plant physics and check whether the "
            "trajectory stays inside the safe set, compared against doing "
            "nothing. This is the check that catches a correctly diagnosed "
            "hazard met with an insufficient response."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "plant_state": {
                    "type": "object",
                    "description": "Current measured state, e.g. {'T': 355.0} for the CSTR.",
                },
                "cartridge": {"type": "string", "default": "cstr"},
                "params": {"type": "object"},
                "horizon_s": {"type": "number", "default": 1200},
            },
            "required": ["action", "plant_state"],
        },
    },
    "admit_explain": {
        "fn": tool_explain,
        "description": "Explain an ADMIT violation code (e.g. AAS-T3-STEP-MISSING) and how to fix it.",
        "schema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
}


# ------------------------------------------------------------------ protocol


def _result(payload: dict, is_error: bool = False) -> dict:
    text = json.dumps(payload, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(request: dict) -> dict | None:
    """One JSON-RPC request in, one response out. None means notification."""
    method = request.get("method")
    req_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        # Enumerate cartridges at list time, not import time, so a client sees
        # the operator's own plants without the server needing a restart.
        names = available_cartridges()
        tools = []
        for name, spec in TOOLS.items():
            schema = json.loads(json.dumps(spec["schema"]))
            prop = (schema.get("properties") or {}).get("cartridge")
            if prop is not None:
                prop["enum"] = names
            tools.append(
                {"name": name, "description": spec["description"], "inputSchema": schema}
            )
        result = {"tools": tools}
    elif method == "tools/call":
        name = params.get("name")
        spec = TOOLS.get(name)
        if spec is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"unknown tool {name!r}"},
            }
        args = params.get("arguments") or {}
        try:
            result = _result(spec["fn"](**args))
        except ToolError as exc:
            result = _result({"error": str(exc)}, is_error=True)
        except TypeError as exc:
            result = _result({"error": f"bad arguments for {name}: {exc}"}, is_error=True)
        except Exception:  # never take the server down on one bad call
            result = _result(
                {"error": "internal_error", "message": "unexpected server failure"},
                is_error=True,
            )
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    if req_id is None:
        return None
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for raw in stdin:
        if len(raw.encode("utf-8", errors="replace")) > MAX_STDIO_LINE_BYTES:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": f"request exceeds {MAX_STDIO_LINE_BYTES} byte limit",
                },
            }
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue  # a malformed frame is not worth killing the session over
        response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main() -> None:
    serve()


if __name__ == "__main__":
    main()
