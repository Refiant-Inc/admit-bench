# IFAC conference runbook

This is the short operational checklist for the ADMIT live CSTR demonstrator.
The demonstrator is deterministic and offline-capable; it is a benchmark model,
not a plant digital twin.

## Before the venue opens

From the repository root, use the same Python environment that will be used on
the show floor:

```bash
pip install -e ".[demo]"
admitbench doctor
python -m pytest tests/ifac_demo -q
admitbench ifac-demo
```

Open `http://127.0.0.1:8000` and complete one consent-declined rehearsal and
one consented rehearsal. Confirm that:

- the header says **ONLINE**;
- the fault appears at 30 simulated seconds and the decision pauses near 358 K;
- an admissible controlled shutdown or sufficiently strong cooling action is
  applied once and produces a measured outcome;
- an inadmissible hold is blocked and the process remains visibly paused;
- **Start new participant** returns temperature, clock, consent, and decision
  state to their initial values;
- `/research` shows only aggregates and both export formats download when
  authorized.

Keep the server terminal available but out of the participant display. Use the
header **Fullscreen** control after the browser is on the intended monitor.
Prevent operating-system sleep, notifications, automatic updates, and display
scaling changes for the session. Do not depend on venue internet access.

## Starting the conference session

Use the local-only default whenever the participant uses the same computer:

```bash
admitbench ifac-demo --host 127.0.0.1 --port 8000 --data-dir data/ifac_demo
```

If a non-local bind is genuinely required, set a strong
`ADMIT_IFAC_ADMIN_TOKEN` before starting. The server refuses a non-local bind
without it. Treat network exposure, reverse-proxy logs, and venue logging as a
separate privacy review; the application cannot control those systems.

## Per participant

1. Select **Start new participant** and verify `0:00`, `350.0 K`, **RESEARCH
   OFF**, and **ARMED**.
2. Let the participant read the consent text and make their own choice.
3. Do not coach the diagnosis or action unless the study protocol permits it.
4. After submission, point out the real T0-T6 result and the measured outcome.
5. For a blocked action, explain that no process action was executed and the
   state is intentionally paused. Do not imply that ADMIT automatically chose
   a substitute action.
6. Reset before handing the interface to the next participant.

## Recovery during a demonstration

- **Browser refresh:** safe. The page reloads the authoritative server state.
- **Lost action response:** wait for the connection indicator to return
  **ONLINE**, then retry the unchanged proposal. Its idempotency key prevents a
  second execution or research row.
- **OFFLINE indicator:** confirm the server process is still running. The page
  retries automatically. Restart the browser only after checking the server.
- **Server process stopped:** restart it and begin a new participant. Active
  plant/session state is intentionally memory-only and is not reconstructed
  from research records.
- **Blocked session:** use **Start new participant**. The plant clock cannot be
  advanced after a blocked decision.
- **Unexpected display behavior:** exit fullscreen, refresh, and check browser
  zoom is 100%. If the state is unclear, reset rather than narrating an
  uncertain result.

## Research data and shutdown

Declining consent produces no participant UUID or participant record.
Consented JSONL records and exports are stored under `data/ifac_demo/`, which is
ignored by Git. Before collection, the study owner must define the lawful
basis, retention period, approved storage location, access policy, and deletion
procedure. Do not copy these records into benchmark outputs or source control.

At the end of the day:

1. Finish or reset the active participant session.
2. Export the required aggregate source data from `/research` to the approved
   encrypted location.
3. Record the export time and operator according to the study protocol.
4. Stop the server with `Ctrl+C`.
5. Retain or delete local `data/ifac_demo/` only under the approved study data
   policy. Deletion is deliberately not exposed as a public web endpoint.

## Final go/no-go

Do not open collection if the doctor or IFAC tests fail, the connection status
does not stay online, reset does not restore the initial state, a retry creates
two executions/records, research consent language differs from the approved
protocol, or the deployment's logging/retention behavior is unknown.
