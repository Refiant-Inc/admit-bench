# IFAC CSTR demonstrator architecture

Status: Phase 1 repository audit plus Phase 2-6 implementation
(2026-08-21). No benchmark or governance behaviour has been changed.

## Audit summary

ADMIT Bench already contains the two scientific components the demonstrator
must preserve:

- `admitbench.physics.CSTRWorld` is a deterministic first-principles CSTR
  model. It exposes the real state (`Ca`, `T`, `coolant_flow`, `feed_frac`, and
  `shutdown`), RK4 stepping, action-to-control mapping, safe-envelope margin,
  finite-horizon projection, and recovery rehearsal.
- `admitbench.gates.run_gates` is the canonical full governance pipeline. It
  composes the record/evidence/authority checks with hazard understanding and
  physical consequence verification, returning a `GateReport` and replayable
  trajectory artifacts.

The repository does **not** currently contain a live mutable process session,
fault scheduler, HTTP API, consent-aware participant store, or interactive
frontend. The existing dashboards are self-contained static HTML reports over
completed benchmark traces. The demo should add adapters around the existing
kernel, not a second simulator or a second governance implementation.

## Existing components to reuse

| Concern | Canonical implementation | Reuse in the demo |
|---|---|---|
| CSTR dynamics | `admitbench/physics.py`: `World`, `CSTRWorld` | Use `initial_state`, `step`, `apply_action`, `margin`, `envelope`, `project`, and `rehearse_recovery` directly. `T=370 K` remains the existing trip boundary. |
| CSTR action space | `admitbench/cartridges/cstr/manifest.yaml` and `admitbench/rulebook.py` | Generate participant controls from the loaded `RuleBook`; do not maintain a frontend action allow-list. The physical actions already supported are coolant-flow increase, feed reduction, FC-201 setpoint adjustment, and controlled shutdown. Hold, observe, rehearse, and escalation are intentional null-physics actions. |
| Fault/scenario material | `admitbench/cartridges/cstr/procedures_cases.jsonl` | Reuse the cooling degradation and imminent-runaway states/evidence patterns in C01/C07/C09. C07 is the strongest conference default: visible deviation, multiple plausible actions, a real unsafe-delay result, and a deterministic 222 s null crossing from its decision state. |
| Plant metadata | `admitbench/cartridges/cstr/system_graph.jsonl` | Drive tag names, units, nominal values, alarm/trip limits, and the process schematic. |
| Hazards and recovery | `admitbench/cartridges/cstr/safety_case_graph.jsonl` | Reuse validated hazard IDs, cause/effect entries, forbidden actions, and recovery plans. Candidate knowledge must remain visibly unverified and must not become a hard rule. |
| Procedures | `admitbench/cartridges/cstr/procedures_cases.jsonl` | Reuse SOP steps attached by `Cartridge.load` to the same rules enforced at T3. A demo check is recorded only after the corresponding server-side verification actually ran. |
| Proposed action | `admitbench/record.py`: `ActionRecord` | This is the canonical `ProposedAction`. The human adapter produces it; a future LLM adapter may use the existing parser/provider route to produce the identical object. |
| Evidence | `admitbench/record.py`: `Evidence`, `EvidenceLog` | Admit visible simulator observations through the cartridge trust map, with episode time, quality, source, tag, value, unit, and hash-chain attestation. The client cannot assert trust. |
| Authority/context | `admitbench/record.py`: `DecisionContext` | Use cartridge authority scope and current session mode; server time supplies `decided_at`. |
| Record checks | `admitbench/checker.py` | Reuse T0 record/parameter validation, T1 cited-evidence validation, and T3 authority/mode/SOP/confidence/reversibility checks unchanged. |
| Full decision | `admitbench/gates.py`: `run_gates` | The only governance evaluation used by the demo. T0-T4 remain hard gates and T6 remains the audit result. |
| Verdict/scoring | `admitbench/scoring.py` | Preserve `admissible`, `inadmissible`, and `not_evaluable`; do not let a score rescue a failed gate. Aggregate/T5 may be retained in the debug/research trace but is not the participant-facing authorization signal. |
| Trace shapes/provenance | `admitbench/runner.py`, `admitbench/provenance.py` | Reuse serializable record, gate, evidence, trajectory, cartridge-hash, and git-revision shapes. Do not call `run_episode` for human sessions because it invokes a provider and always constructs a benchmark trace. |
| Existing visualization | `admitbench/dashboard.py`, `admitbench/dashboard_ui.py` | Reuse visual language and aggregate-calculation ideas where useful. These modules generate post-run static HTML; they are not a live participant UI or API. |
| Tests | `tests/base`, `tests/stress`, and relevant `tests/unit` modules | Keep all kernel invariants, sealing, deterministic replay, physics, gate ordering, parameter hardening, and dashboard tests. Add demo-specific tests beside them. |

There is no existing web framework dependency or application server. The
package currently depends only on PyYAML and exposes the `admitbench` CLI.
Phase 3 should therefore add the web dependencies as an optional `demo` extra,
not burden the benchmark-only installation.

## Governance concepts shown to participants

The UI will report the real tiers and map them to concise conference language:

| Participant label | Canonical source |
|---|---|
| Record | T0 safety-case compilation and record integrity |
| Evidence | T1 evidence and state validity |
| Hazard understanding | T2 hazard and causal understanding |
| Authority, scope, and procedure | T3 action admissibility violations (`AUTHORITY`, `MODE`, `STEP_*`, confidence, and reversibility) |
| Physical consequence | T4 envelope, worse-than-null, recovery, and unsafe-delay checks |
| Audit | T6 traceability information and warnings |

“Reversibility” is not an independent gate in the current implementation; it
is enforced at T3 and, when a costly/permanent commit names a machine-checkable
recovery action, rehearsed at T4. “Escalation” is an action class, not a fourth
canonical verdict. Consequently:

- a normal intervention is executed only when the canonical verdict is
  `admissible`;
- `inadmissible` is displayed as **BLOCKED** and the first/all real violations
  explain why;
- `not_evaluable` is displayed as **UNRESOLVED — HOLD**;
- an admissible `escalate_to_operator` action is displayed as **ESCALATION
  INITIATED** but remains internally an admissible action;
- the UI must not claim that ADMIT automatically escalated a blocked action.
  It may show the existing violation guidance to hand the decision up.

## Small adapters required

1. **Live plant session.** Wrap one `CSTRWorld` and a mutable state dictionary.
   Advance it by calling `world.step` at deterministic simulation intervals;
   preserve the actual state history for charts. Inject a configured fault by
   changing the real state/control field named by the scenario. Reset creates a
   fresh world, state, clock, history, evidence log, and state-machine instance.
2. **Live decision case.** At a decision point, create a runtime `Case` using
   the current state, configured sealed hazard truth, diagnosis vocabulary, and
   observations admitted to the live `EvidenceLog`. This supplies the existing
   `run_gates` signature without editing the shipped cartridge cases.
3. **Human proposer.** Convert controlled UI input into `ActionRecord`. Action
   and required parameter metadata come from the rulebook. Cited evidence and
   completed checks come from server-recorded interactions; reversibility comes
   from the rule, and retry keys for non-retry-safe actions are server-issued.
4. **Decision presenter.** Convert `GateReport.to_dict()` and trajectory
   artifacts into a small participant view while retaining the untouched raw
   output in a developer panel.
5. **Execution gateway.** Under the same session lock and state version used
   for evaluation, call `world.apply_action` only when no hard gate failed.
   The UI has no endpoint that directly mutates plant controls.
6. **Consent-aware research store.** Persist the minimum participant record
   only when the server-side session consent flag is true. Reuse ADMIT’s
   serializable evidence/gate/trajectory shapes, but do not reuse the benchmark
   runner’s unconditional model trace writer.

## Planned new files

The intended Phase 2-6 layout is:

```text
admitbench/ifac_demo/
  __init__.py
  models.py          # demo states, scenario/action/request/response schemas
  proposers.py       # ActionProposer protocol and HumanActionProposer
  session.py         # live CSTR clock, fault injection, evidence, reset/state machine
  governance.py      # thin run_gates adapter and participant-safe presentation
  gateway.py         # sole evaluate-then-conditionally-apply path
  research.py        # consent gate, JSONL/CSV export, aggregate metrics
  server.py          # conference/research HTTP API
  static/
    index.html
    app.js
    styles.css
    research.html
admitbench/ifac_demo/scenarios/
  conference_cstr.yaml
tests/ifac_demo/
  test_session.py
  test_gateway.py
  test_proposers.py
  test_consent.py
  test_end_to_end.py
docs/IFAC_DEMO.md
```

`pyproject.toml` will receive an optional `demo` dependency group and a CLI
entry or subcommand. `.gitignore` will include `data/ifac_demo/`; no participant
records belong in source control. Exact filenames may be collapsed if an
adapter remains trivial, but the execution gateway and consent boundary should
remain explicit modules because they carry safety/privacy invariants.

Phase 2 has implemented `models.py`, `proposers.py`, `session.py`,
`governance.py`, `gateway.py`, the deterministic conference scenario, and the
corresponding backend tests. The web server/static UI and research modules are
intentionally deferred to Phases 3 and 4. The implemented live clock pauses at
the decision boundary, evaluates the exact state version projected at T4, and
continues outcome simulation only after an admissible action; blocked sessions
remain paused and unchanged.

Phases 3 and 4 implemented the localhost conference API, self-contained
participant frontend, optional admin page, consent-gated minimal JSONL records,
aggregate summary, and JSONL/CSV exports. Phase 5 adds aggregate-only research
visualizations for decision outcomes, failure codes, action selection,
scenario-level admissibility, response times, and projected process outcomes.
No raw-record route is exposed to the dashboard. Phase 6 hardens the conference
path with idempotent action submissions, reconnect/refresh recovery, fullscreen
and accessibility affordances, measured post-action recovery summaries, an
explicit paused disposition for blocked sessions, wheel asset verification,
and an operator runbook.

## Session and data flow

```text
scenario YAML + CSTR cartridge
              |
              v
LiveCSTRSession --step(CSTRWorld)--> real state/history --> browser plots
       |                                      |
       | configured fault                     | visible observations only
       v                                      v
runtime state + EvidenceLog <------- HumanActionProposer
       |                               produces ActionRecord
       +--------------------+-----------------+
                            v
                    run_gates (T0-T6)
                            |
                  GateReport + artifacts
                            |
                     ExecutionGateway
                    /                 \
           admissible                 hard failure/unresolved
          apply_action                 leave state unchanged
                    \                 /
                     v               v
                  live session + participant decision view
                                      |
                         consented minimum record only
```

The explicit demo state machine is `WELCOME -> CONSENT -> NOMINAL_OPERATION ->
FAULT_INTRODUCED -> HUMAN_DIAGNOSIS -> HUMAN_ACTION_SELECTION ->
ADMIT_EVALUATION -> EXECUTE|HOLD -> OUTCOME -> SESSION_SUMMARY`. The live clock
pauses from the decision point through atomic evaluation/application so the
state projected by T4 is the state to which the verdict applies.

The default conference scenario should use the existing cooling-loss/runaway
mechanism: begin at the nominal CSTR steady state, degrade coolant flow at a
configured deterministic time, and generate TT-101/FT-201/FC-201 observations
from the resulting simulator state. Hidden scenario fields such as the fault
cause and exact hazard countdown stay server-side. Conference controls should
include only actions loaded from the cartridge and supported by
`CSTRWorld.apply_action`; developer mode may expose the complete grammar.

## Human-to-LLM replacement boundary

`ActionProposer` should accept the same observation bundle and return the same
`ActionRecord` regardless of proposer:

```python
class ActionProposer(Protocol):
    def propose(self, observation: Observation) -> ActionRecord: ...
```

`HumanActionProposer` consumes validated UI selections. A future
`LLMActionProposer` can reuse `providers.py`, `prompts.py`, and
`parser.parse_action_record`; it must not receive sealed scenario truth. Both
then enter the identical `run_gates -> ExecutionGateway -> CSTRWorld` path.
No LLM package or API key is required by conference mode.

## Research and privacy boundary

Declining consent creates no session UUID and no participant-level disk write.
Consenting creates a random UUID and records scenario/version, visible
observations, selected action/reasoning, response duration, canonical gate
result, whether execution occurred, and pre/post/outcome state. Names, email,
IP address, user agent, browser fingerprint, location, and device identifiers
are outside the schema.

The UI should say “anonymous” only for a local deployment whose HTTP access
logging is disabled and whose hosting layer is known not to retain identifiers.
Otherwise it should say “de-identified participant record” and disclose that
infrastructure logs may exist. JSONL/CSV exports and the aggregate `/research`
view read only consented records and do not expose raw records on the public
screen.

## Phase 1 verification and known baseline issues

The focused kernel/CSTR suite passed before implementation:

```text
89 passed
```

This covered base invariants, stress tests, CSTR physics, gates/scoring,
records, rulebook/cartridge loading, and existing dashboards under the
repository’s CI setting `PYTHONUTF8=1`.

The full untouched suite produced `299 passed, 2 failed` locally. One failure
is a Windows default-encoding read in `test_dashboard_html_is_self_contained`
and passes with the repository’s documented CI UTF-8 environment. The other is
the pre-existing byte-reproduction check for
`industrialbench_layer1_generated_420.jsonl`; it is unrelated to the IFAC CSTR
path and must not be “fixed” by changing frozen generated benchmark data as
part of this demo.

## Scientific interpretation constraints

`docs/SIMULATOR_VALIDITY.md` is load-bearing documentation for the conference
story: this is a deterministic benchmark simulator, not a validated plant twin
or formal safety proof. Its CSTR boundary cases are sensitive to several
physical parameters. The participant UI and study documentation must describe
T4 as a finite-horizon result under the stated benchmark model and must not
present it as plant-grade prediction.

`CSTRWorld` has one `coolant_flow` state rather than separate commanded-flow
and measured-flow dynamics. Phase 2 therefore derives both the FT-201 display
and FC-201 control/check view from that same real simulator value. They are not
independent corroborating measurements, and the UI/study analysis must not
describe them as such. Adding actuator/sensor separation would be a simulator
fidelity change and is outside the demo adapter unless made explicitly and
validated separately.
