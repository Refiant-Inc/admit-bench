# IFAC live CSTR demonstrator

## Purpose

The demonstrator makes one boundary visible:

```text
Human proposes an intervention
             ↓
ADMIT evaluates the real record, evidence, authority, procedure, and trajectory
             ↓
Only an admissible intervention reaches the live benchmark CSTR
```

The current proposer is a conference participant. A future LLM proposer returns
the same canonical `ActionRecord` and enters the same governance and execution
path.

## Install and start

From the repository root:

```bash
pip install -e ".[demo]"
admitbench ifac-demo
```

Open `http://127.0.0.1:8000`. The command binds only to the local machine and
disables Uvicorn access logs by default. No model API key or network connection
is required.

Useful options:

```bash
admitbench ifac-demo --port 8080
admitbench ifac-demo --scenario path/to/scenario.yaml
admitbench ifac-demo --data-dir data/ifac_demo
```

A non-local bind such as `--host 0.0.0.0` is rejected unless
`ADMIT_IFAC_ADMIN_TOKEN` is set, because the server includes research export
endpoints. Send that value in the `X-ADMIT-Admin-Token` header or enter it on
the research page.

## Conference flow

1. Select **Begin demonstration**.
2. Consent to optional research logging or continue without recording. Both
   choices run the complete demonstration.
3. Watch nominal operation. At 30 simulated seconds the configured cooling
   disturbance is introduced into the real CSTR state.
4. The simulator pauses near 358 K and asks for a diagnosis and intervention.
5. Select evidence, perform any required SOP checks, choose a magnitude and
   confidence, and submit.
6. The server converts the controlled input to `ActionRecord`, calls the real
   `run_gates` T0-T6 chain, and shows its result.
7. The live execution gateway applies the action only if the canonical verdict
   is admissible. Outcome simulation then continues from the governed state.
8. Use **Start new participant** anywhere in the interface to reset the clock,
   plant, evidence ledger, decision state, consent, and research session.

The action request carries a browser-generated idempotency key. If a response
is lost and the browser retries the same proposal, the server returns the
existing decision without evaluating, executing, or recording it again. A page
refresh reloads the authoritative in-memory session from the server. The
connection indicator turns red while the server is unavailable and retries in
the background.

After an authorized action, the outcome card is derived from the real live
history: maximum reactor temperature, current margin to the 370 K boundary,
whether the trajectory returned to the configured nominal band (at or below
352 K), recovery time, and whether the trip boundary was crossed. These are
measured demo-session values, distinct from the finite-horizon projection
stored at decision time. The final summary becomes available after the fixed
300-second simulated outcome window completes. A blocked proposal has no invented automatic
fallback: the authoritative process remains paused at the decision state until
the facilitator starts a new participant session.

The participant page is self-contained and loads no third-party fonts,
analytics, scripts, images, or CDNs.

For venue setup, operation, incident recovery, and end-of-day data handling,
use [IFAC_CONFERENCE_RUNBOOK.md](IFAC_CONFERENCE_RUNBOOK.md).

## Architecture

- `ifac_demo/session.py` owns the mutable CSTR state, deterministic fault,
  visible evidence ledger, state machine, and reset.
- `ifac_demo/proposers.py` maps human input to the canonical record. It derives
  reversibility, recovery, and retry behaviour from the loaded cartridge.
- `ifac_demo/governance.py` calls `run_gates`; it contains no duplicate policy.
- `ifac_demo/gateway.py` holds the atomic evaluate/apply boundary and rejects a
  hard-failed or stale authorization.
- `ifac_demo/controller.py` exposes conference operations and joins optional
  research recording to completed decisions.
- `ifac_demo/server.py` exposes the local API and static participant/admin
  pages. The browser has no direct plant-control endpoint.

See [IFAC_DEMO_ARCHITECTURE.md](IFAC_DEMO_ARCHITECTURE.md) for the repository
audit and detailed data flow.

## Research logging and privacy

Research logging is opt-in. Declining consent creates no session UUID and no
participant record. Consenting creates a random UUID in server memory and
writes one minimal record when an intervention is submitted.

Stored fields are limited to:

- random session UUID, study/scenario version, and timestamp;
- signals that were visible at decision time;
- selected action and parameters;
- response duration;
- canonical ADMIT gates, verdict, and failure codes;
- whether execution occurred and the simulated pre/post/outcome state.

The application does not request or intentionally store names, email
addresses, IP addresses, user-agent strings, browser fingerprints, precise
location, or device identifiers. Optional free-text reasoning is used only in
the in-memory action record and is deliberately omitted from research files.
The store rejects known identifying-field names defensively.

This supports a **de-identified application record**. Do not claim stronger
anonymity without checking the complete deployment: reverse proxies, operating
systems, venue networks, and hosting platforms may retain identifiers outside
this application. The bundled local server disables its own access log.

Records and exports are written beneath:

```text
data/ifac_demo/
  sessions/<random-uuid>.jsonl
  exports/ifac_demo_<timestamp>_<suffix>.jsonl
  exports/ifac_demo_<timestamp>_<suffix>.csv
```

That directory is ignored by Git. Never copy participant data into committed
benchmark results.

## Research export

Open `http://127.0.0.1:8000/research`. The page derives the following views
from consented records and does not display individual raw records:

- admissible, blocked, escalated, and unresolved decision distribution;
- failed governance check/code distribution;
- participant action selection distribution;
- admissibility by scenario;
- response-time histogram and median;
- aggregate projected process outcomes.

It also downloads JSONL or CSV exports for offline analysis.

The equivalent API endpoints are:

```text
GET  /api/research/summary
POST /api/research/export/jsonl
POST /api/research/export/csv
```

When an admin token is configured, include it as `X-ADMIT-Admin-Token`.

## Scenario configuration

The conference default is
`admitbench/ifac_demo/scenarios/conference_cstr.yaml`. It references the
existing C07 hazard semantics, starts from the existing nominal CSTR state,
sets the existing `coolant_flow` state to 40% at the configured fault time,
and pauses for a human decision at a configured display threshold. That
threshold controls demo timing only; T4 continues to use the canonical 370 K
safe-envelope boundary from `CSTRWorld`.

`participant_actions` selects a conference-friendly subset of names from the
loaded cartridge rulebook. It cannot define actions or governance rules;
unknown names fail scenario construction.

The conference scenario has no stochastic physics or fault sampling, so no
random seed is needed: the same YAML, cartridge, and participant action produce
the same plant trajectory and gate result. The only random value is a consented
research session UUID, which does not affect simulation or governance. A future
randomized research mode should select among versioned scenario files and log
that selection; it must not mutate the reference contract silently.

## Adding a fault or scenario

Copy `conference_cstr.yaml`, give it a new stable scenario and study version,
and change only fields supported by `ScenarioConfig`: source case, fault time,
real CSTR state overrides, decision variable/threshold, diagnosis choices, and
participant action names. Every action name is validated against the loaded
cartridge rulebook. Start it with `admitbench ifac-demo --scenario <path>` and
add a deterministic test proving the fault time, visible decision state, at
least one genuine admissible path, and at least one genuine blocked path.

Do not place hidden fault truth in participant evidence, invent frontend-only
actions, or change envelope/governance thresholds merely to obtain a desired
verdict. If new physics is needed, validate it in the benchmark world and
cartridge first; the demo configuration is not the place to introduce it.

## Adding another simulator

The proposer, canonical `ActionRecord`, governance adapter, execution-gateway
rule, consent store, and research aggregates are simulator-independent. Add a
sibling live-session adapter that provides the same operations used by
`DemoController`: authoritative state/history, deterministic advance/fault,
participant-visible evidence, a runtime cartridge case, atomic state version,
and guarded `_apply_authorized`. Load actions and procedures from that
simulator's cartridge and call its existing `World` methods.

The current participant page is intentionally CSTR-specific, so add or select
a presentation component for the new world's real variables and limits. Keep
the browser read/propose-only: it must still submit through the same server
action route and `ExecutionGateway`. Before exposing the new simulator, repeat
the blocked-cannot-execute, reset, deterministic replay, consent, and wheel
asset tests.

## Interpretation limits

The CSTR is a deterministic first-principles benchmark model, not a validated
plant twin or a formal proof. T4 means finite-horizon consequence under this
model. See [SIMULATOR_VALIDITY.md](SIMULATOR_VALIDITY.md) for parameter
sensitivity and the intended validity domain.

The current physics model has one `coolant_flow` state, so FT-201 and the
FC-201 control view are two presentations of the same modeled value, not
independent sensor corroboration. Study analysis must preserve that caveat.

## Future LLM proposer

Implement the `ActionProposer` protocol and return `ActionRecord`. Existing
provider, prompt, and parser modules can form an `LLMActionProposer`; it must
receive only participant-visible observations, not sealed case truth. No code
downstream of the proposer changes:

```text
LLMActionProposer → ActionRecord → run_gates → ExecutionGateway → CSTRWorld
```
