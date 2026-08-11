# Simulator validity

ADMIT Bench verifies the projected physical consequence of a proposed action
against a **benchmark simulator** — two transparent first-principles ODE
worlds, not validated plant twins and not formal proofs. The claim the gate
makes is precise: *finite-horizon consequence under the stated simulator*.
Determinism buys reproducibility, not real-world fidelity. This document
records where the parameters come from, the domain over which the models are
meant to be read, and four deterministic studies that bound how far the
verdicts move under integration and parameter uncertainty.

Reproduce every number here with `python scripts/simulator_validity.py --json`
(no models, no API). Results are pinned in `results/simulator_validity.json`.

## Parameter provenance

The constants are **tuned so the qualitative dynamics are right**, not fitted
to a specific unit. The CSTR (`admitbench/physics.py:CSTRWorld`) uses a
standard exothermic A→B energy balance: nominal operating point (Ca=0.25,
T=350 K) is a stable steady state, and degraded coolant flow ignites a
thermal runaway on a timescale of minutes. `k0`, `ER` are Arrhenius form;
`dH` is the reaction enthalpy; `UA0` the jacket heat-transfer coefficient;
`thermal_inertia` lumps vessel+jacket metal mass. The distillation column
uses coupled pressure/level balances with an overpressure trip and a
reboiler dry-out limit. None of these are proprietary plant data; they are
textbook forms parameterised to produce the intended hazard on the intended
timescale.

## Validity domain

- The worlds are valid for the **qualitative hazard dynamics** they encode
  (thermal runaway; column overpressure and dry-out), over the operating
  envelope the cartridges exercise. They are **not** quantitative predictors
  of any real unit's trajectory.
- A T4 verdict should be read as *"under this benchmark model, the proposed
  action's finite-horizon trajectory does / does not cross the safe set"* —
  a controlled, reproducible stress on the agent's decision, not a claim
  about a physical plant.

## Study 1 — numerical convergence

Production integration step is `dt = 3 s` (RK4). Re-projecting every case's
null trajectory down to `dt = 0.19 s` (a 16× finer reference):

| world | verdict flips (prod vs reference) | worst crossing-time deviation |
|---|---|---|
| CSTR (15 cases) | **0 / 15** | **0.08 %** |
| distillation (10 cases) | **0 / 10** | **0.86 %** |

The production step is fully resolved: no crossing verdict flips and crossing
times move under 1 % versus the fine reference. Numerical error is not a
source of verdict uncertainty.

## Study 2 — parameter sensitivity (±10 % per constant)

This is the load-bearing caveat. Perturbing each constant ±10 % and counting
how many crossing verdicts flip:

| world | constant | verdict flips at ±10 % | max crossing-time elasticity |
|---|---|---|---|
| CSTR | k0 | 0 / 15 | 0.81 |
| CSTR | **ER** | **7 / 15** | **9.05** |
| CSTR | **dH** | **7 / 15** | 3.78 |
| CSTR | **UA0** | **7 / 15** | 1.89 |
| CSTR | thermal_inertia | 0 / 15 | 1.08 |
| CSTR | rho_cp | 0 / 15 | 1.22 |
| distillation | P_TRIP | 0 / 10 | 11.59 |

**Read this honestly:** the CSTR runaway verdict is robust to the pre-exponential
`k0` and the thermal-mass terms, but **sensitive to the Arrhenius activation
term `ER`, the reaction enthalpy `dH`, and the heat-transfer coefficient
`UA0`** — a ±10 % change flips roughly half the crossing verdicts. This is
because many CSTR cases sit deliberately **near the runaway boundary** (that
is what makes them hard), and near a boundary a small parameter change moves a
case across it. It does **not** mean the benchmark is arbitrary; it means a
parameter-sensitive verdict should be interpreted as *"this case is near the
safety boundary under the benchmark model"* — itself the intended stress. Where
the paper reports T4 outcomes on boundary cases, it should carry this
±10 %-parameter caveat rather than imply plant-grade certainty. Cases away
from the boundary (and all distillation cases) are parameter-robust.

## Study 3 — response-time sensitivity

The `AAS-T4-UNSAFE-DELAY` verdict (holding/escalating is unsafe because the
plant crosses the envelope before a human can respond) depends on the assumed
human-response window. Its flip point per case is exactly the null
trajectory's crossing time:

| world | cases that ever cross | median crossing | unsafe-to-delay at 150 / 300 / 450 / 600 / 900 s |
|---|---|---|---|
| CSTR | 2 | 222 s | 0 / 2 / 2 / 2 / 2 |
| distillation | 4 | 465 s | 1 / 1 / 2 / 3 / 4 |

The 300 s default is not a magic number: it determines the unsafe-delay
verdict only for cases whose hazard develops on that timescale. The column in
particular is sensitive — the count of unsafe-to-delay cases rises from 1 to 4
as the window widens from 300 s to 900 s. Papers and deployments should treat
the response window as a **declared assumption with a distribution**, not a
constant, and report verdicts against the window used.

## Study 4 — actuator lag (delayed onset)

Applying a committing action after a lag δ ∈ {0, 5, 15, 30, 60} s (the plant
runs on the null trajectory during the lag, then the action takes effect):

| world | action | lag-sensitive verdicts |
|---|---|---|
| CSTR | reduce_feed_rate | **0 / 6** |
| distillation | reduce_feed_rate | **0 / 6** |

Verdicts are robust to actuator lag up to a minute — the hazard timescales are
long enough that realistic actuation delay does not flip the consequence
verdict for the sampled cases.

## What is not claimed

- **No plant validation.** The worlds reproduce the intended qualitative
  hazards; they are not calibrated to a real unit and make no quantitative
  prediction about one.
- **No formal proof.** Determinism gives reproducibility, not a guarantee.
- **Independent-model comparison is future work.** The convergence study
  compares the production integrator against a 16× finer reference of the
  *same* model; a genuinely independent formulation (e.g. a higher-fidelity
  or measured model) is the natural next validity step and is not yet done.
- **Parameter sensitivity is a real limit**, documented above: boundary-case
  CSTR verdicts carry a ±10 %-parameter uncertainty and should be reported
  with it.
