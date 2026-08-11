# ADMIT Bench as an Agent Plugin

The benchmark asks whether an agent's action *would have been* admissible. This
packages the same gates so an agent can ask before it acts.

`checker.py` carries the line *"same function at rehearsal, gate, and audit —
one check, not three systems."* The bench is the rehearsal. This is the gate.

Conforms to [Agent Plugins 1.0.0](https://agent-plugins.org).

## Install

See [INSTALL.md](INSTALL.md) for the full checklist.

**Production (wheel, no checkout):**

```bash
pip install "admit-bench[plugin]"
admitbench plugin-root   # point your Agent Plugins client here
```

**Developer checkout:**

```bash
pip install -e ".[dev,plugin]"
# or: ./plugin/scripts/bootstrap_venv.sh
```

`mcp.json` launches `admit-bench-mcp` over stdio (packaged entry point). The
checkout shim `plugin/server/admit_mcp.py` delegates to the same server. No new
dependencies on the wire — stdlib JSON-RPC only.

## Tools

| tool | what it answers |
|---|---|
| `admit_full_check` | **preferred** — full T0–T4 path via `run_gates` (record + evidence + authority + consequence) in one verdict |
| `admit_describe_contract` | what a valid record looks like here, and every action's authority, evidence, and reversibility requirements |
| `admit_check_record` | record-level only (T0/T1/T3): evidence, authority, procedure, reversibility — no physics |
| `admit_verify_consequence` | T4 alone: projected trajectory vs doing nothing |
| `admit_explain` | what a violation code means and how to fix the record |
| `admit_list_cartridges` | which plants this server can gate against |

The intended loop is `describe_contract` once, then **`admit_full_check`**
before every action that touches the plant, with `explain` on any code that
comes back. Split tools remain for staged debugging; integrators should not
treat `check_record` alone as a complete gate for commit-class actions.

## Example

Ask whether a 5% coolant increase is admissible for a reactor at 366 K
(full T0–T4 path):

```json
{
  "name": "admit_full_check",
  "arguments": {
    "record": {
      "action": "increase_coolant_flow",
      "params": {"delta_pct": 5},
      "cited_evidence": ["E1"],
      "checks_performed": ["confirm_tt101_rising"],
      "confidence": 0.9,
      "reversibility": "undoable",
      "recovery_plan": "revert to the previous setpoint"
    },
    "evidence": [
      {"id": "E1", "tag": "TT-101", "value": 366.0, "source": "control_system", "received_at": 0}
    ],
    "plant_state": {"T": 366.0, "coolant_flow": 40.0},
    "decided_at": 10
  }
}
```

```json
{
  "admissible": false,
  "hard_failed": true,
  "violations": [{
    "code": "AAS-T4-ENVELOPE",
    "gate": "T4",
    "message": "projected trajectory under increase_coolant_flow crosses the safe envelope (T at t=78s)"
  }],
  "next_step": "Call admit_explain on the first code, revise the record, re-check with admit_full_check."
}
```

The diagnosis was right and the action was the right *kind*. It was too small,
and only the physics says so.

## Skill

`skills/authoring-cartridges` covers turning plant documentation into a
cartridge, so the gates run against your plant rather than a reference one.
Authoring may use a model freely; what comes out is deterministic and has to
compile before it runs.

## Two boundaries that hold this apart from the benchmark

**The kernel never learns this exists.** Everything here imports from
`admitbench` and calls it; nothing in `admitbench/` imports from here. The
deterministic path — loader → gates → physics → scoring — keeps zero network
surface, so the reproducibility claim survives having a server in front of it.

**The answer key stays sealed.** No tool reads a case oracle, its accepted
diagnoses, or its acceptable actions. `admit_describe_contract` returns the
*whole* rulebook: a narrowed list would leak which actions the cases care
about. Both properties are asserted in `tests/unit/test_plugin_bridge.py`
against the shipped cartridges, including that the exposed vocabulary is a
strict superset of the oracle actions.

## Your own plants

The two bundled cartridges are a CSTR and a distillation column. To gate
against anything else, author a cartridge (see the skill) and point the server
at it:

```json
"env": { "ADMITBENCH_CARTRIDGE_PATHS": "/opt/plants:/home/me/cartridges" }
```

Opt-in on purpose. Without it the server can only read what ships with the
package, so a confused or compromised client cannot turn a tool call into an
arbitrary filesystem read. Names resolve as bare basenames inside the declared
roots; a path, a traversal, or a symlink pointing out of a root is refused.
`admit_list_cartridges` shows what resolved and names anything that failed to
compile.

## Platform binding (host env)

Set these in the MCP host `env` block when the host — not the model — should
own engagement authority, mode, and evidence admission:

| env | effect |
|---|---|
| `ADMITBENCH_AUTHORITY_SCOPE` | Comma-separated allowlist. If set, omitted `authority_scope` uses this list (clamped to the cartridge manifest); a caller-supplied list may only be a subset. If unset, omitted arg keeps the cartridge agent scope, but callers still cannot invent authorities outside the manifest. |
| `ADMITBENCH_MODE` | If set, forces `DecisionContext.mode` (caller `mode` is ignored). |
| `ADMITBENCH_REQUIRE_PLATFORM_EVIDENCE` | Set to `1` to reject non-empty caller `evidence`; the host must inject platform-attested evidence instead. |

Caller-supplied evidence `trust` fields are always stripped; trust comes only
from the cartridge `trust_map`.

## Scope

This gates *proposed* actions over stdio. It does not execute them, and a
passing verdict is a statement about the record and the model of the plant,
not an authorisation to act on a real one.
