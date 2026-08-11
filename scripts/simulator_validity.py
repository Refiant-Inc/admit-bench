#!/usr/bin/env python3
"""Simulator validity studies for the ADMIT Bench benchmark worlds.

These are DETERMINISTIC (no models, no API). They bound what the consequence
verifier's output means, per reviewer Finding 07: numerical convergence,
parameter sensitivity, response-time sensitivity, and actuator-lag robustness.
Reproducibility, not plant-fidelity, is the claim; these studies say how far
the finite-horizon verdicts move under integration and parameter uncertainty.

    python scripts/simulator_validity.py           # prints tables
    python scripts/simulator_validity.py --json     # also emits results JSON
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from admitbench.cartridge import load_cartridge
from admitbench.physics import world_for

CARTS = {"cstr": "cstr", "distillation": "column"}
PERTURB = {  # constant -> the worlds it lives on
    "cstr": ["k0", "ER", "dH", "UA0", "thermal_inertia", "rho_cp"],
    "column": None,  # discovered below
}


def _cases(name):
    cart = load_cartridge(REPO / "admitbench/cartridges" / name)
    return cart, [(c.id, c.initial_state) for c in cart.cases]


def _fresh_world(world_name):
    return type(world_for(world_name))()


def _project_null(world, init, horizon=1200.0):
    return world.project(world.initial_state(init), None, None, horizon_s=horizon)


# ---------------------------------------------------------------------------
# 1. Numerical convergence — is the production dt (3s) resolved?
# ---------------------------------------------------------------------------

def convergence(name, world_name, cases):
    steps = [3.0, 1.5, 0.75, 0.375, 0.1875]
    rows, worst_dev, flips = [], 0.0, 0
    for cid, init in cases:
        vals = []
        for dt in steps:
            w = _fresh_world(world_name)
            w.dt_s = dt
            t = _project_null(w, init)
            vals.append((t.crossed, t.crossed_at, t.min_margin))
        ref_crossed, ref_at, ref_mm = vals[-1]  # finest dt = reference
        prod_crossed, prod_at, prod_mm = vals[0]  # production dt = 3s
        if prod_crossed != ref_crossed:
            flips += 1
        if ref_crossed and ref_at and prod_at:
            dev = abs(prod_at - ref_at) / ref_at
            worst_dev = max(worst_dev, dev)
        rows.append({"case": cid, "prod_crossed": prod_crossed, "prod_crossed_at": prod_at,
                     "ref_crossed_at": ref_at, "min_margin_prod": round(prod_mm, 4)})
    return {"worst_crossed_at_deviation_pct": round(worst_dev * 100, 2),
            "verdict_flips_prod_vs_reference": flips, "n_cases": len(cases), "rows": rows}


# ---------------------------------------------------------------------------
# 2. Parameter sensitivity — do verdicts survive +/-10% on each constant?
# ---------------------------------------------------------------------------

def parameter_sensitivity(name, world_name, cases, constants, frac=0.10):
    base_verdicts = {}
    for cid, init in cases:
        w = _fresh_world(world_name)
        base_verdicts[cid] = _project_null(w, init).crossed

    results = {}
    for const in constants:
        flips, worst_elastic = 0, 0.0
        for sign in (+1, -1):
            for cid, init in cases:
                w = _fresh_world(world_name)
                base_val = getattr(w, const)
                setattr(w, const, base_val * (1 + sign * frac))
                t = _project_null(w, init)
                if t.crossed != base_verdicts[cid]:
                    flips += 1
                # elasticity of crossed_at, where both cross
                w0 = _fresh_world(world_name)
                t0 = _project_null(w0, init)
                if t0.crossed and t.crossed and t0.crossed_at and t.crossed_at:
                    elastic = abs((t.crossed_at - t0.crossed_at) / t0.crossed_at) / frac
                    worst_elastic = max(worst_elastic, elastic)
        results[const] = {"verdict_flips_at_10pct": flips, "max_crossed_at_elasticity": round(worst_elastic, 2)}
    return results


# ---------------------------------------------------------------------------
# 3. Response-time sensitivity — the "is 300s a promise?" study
# ---------------------------------------------------------------------------

def response_time_sensitivity(name, world_name, cases):
    """For each case, the null trajectory's crossing time IS the response
    window at which holding/escalating flips to an unsafe delay. Report the
    distribution relative to the 300s default."""
    windows = [150, 300, 450, 600, 900]
    per_case, flip_at = [], []
    for cid, init in cases:
        w = _fresh_world(world_name)
        t = _project_null(w, init)
        cross = t.crossed_at if t.crossed else None
        per_case.append({"case": cid, "null_crossed_at_s": round(cross, 1) if cross else None})
        if cross is not None:
            flip_at.append(cross)
    unsafe_at = {win: sum(1 for c in flip_at if c <= win) for win in windows}
    return {"cases_crossing_within_window": unsafe_at,
            "n_cases_that_ever_cross": len(flip_at),
            "median_crossing_s": round(sorted(flip_at)[len(flip_at) // 2], 1) if flip_at else None,
            "per_case": per_case}


# ---------------------------------------------------------------------------
# 4. Actuator lag — delayed onset robustness
# ---------------------------------------------------------------------------

def actuator_lag(name, world_name, cases, action="reduce_feed_rate", params=None):
    """Apply a committing action after a lag delta (project null for delta,
    then act). Report whether the crossing verdict changes as lag grows."""
    params = params or {"delta_pct": 30}
    lags = [0, 5, 15, 30, 60]
    rows = []
    for cid, init in cases[:6]:  # a representative sample
        verdicts = []
        for lag in lags:
            w = _fresh_world(world_name)
            s = w.initial_state(init)
            if lag:
                s = w.project(s, None, None, horizon_s=lag).final_state
            t = w.project(s, action, params, horizon_s=1200.0)
            verdicts.append(t.crossed)
        rows.append({"case": cid, "crossed_by_lag": dict(zip(lags, verdicts)),
                     "verdict_stable": len(set(verdicts)) == 1})
    return {"lags_s": lags, "action": action, "rows": rows,
            "cases_with_lag_sensitive_verdict": sum(1 for r in rows if not r["verdict_stable"])}


def main(emit_json=False):
    out = {}
    for name, world_name in CARTS.items():
        cart, cases = _cases(name)
        constants = PERTURB["cstr"] if world_name == "cstr" else \
            [c for c in ("k_boil", "k_press", "P_TRIP", "level_trip", "reflux_gain", "duty_gain")
             if hasattr(_fresh_world(world_name), c)]
        study = {
            "convergence": convergence(name, world_name, cases),
            "parameter_sensitivity_10pct": parameter_sensitivity(name, world_name, cases, constants),
            "response_time_sensitivity": response_time_sensitivity(name, world_name, cases),
            "actuator_lag": actuator_lag(name, world_name, cases),
        }
        out[name] = study
        print(f"\n{'='*70}\n{name.upper()} ({world_name} world), {len(cases)} cases\n{'='*70}")
        c = study["convergence"]
        print(f"1. CONVERGENCE (production dt=3s vs reference dt=0.19s):")
        print(f"   verdict flips: {c['verdict_flips_prod_vs_reference']}/{c['n_cases']}   "
              f"worst crossed-at deviation: {c['worst_crossed_at_deviation_pct']}%")
        print(f"2. PARAMETER SENSITIVITY (+/-10% each constant, verdict flips / max elasticity):")
        for k, v in study["parameter_sensitivity_10pct"].items():
            print(f"   {k:16s} flips={v['verdict_flips_at_10pct']:<3} elasticity={v['max_crossed_at_elasticity']}")
        r = study["response_time_sensitivity"]
        print(f"3. RESPONSE-TIME SENSITIVITY: {r['n_cases_that_ever_cross']} cases cross; "
              f"median crossing {r['median_crossing_s']}s")
        print(f"   cases unsafe-to-delay at each window: {r['cases_crossing_within_window']}")
        a = study["actuator_lag"]
        print(f"4. ACTUATOR LAG ({a['action']}, lags {a['lags_s']}s): "
              f"lag-sensitive verdicts: {a['cases_with_lag_sensitive_verdict']}/{len(a['rows'])}")

    if emit_json:
        p = REPO / "results" / "simulator_validity.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwritten to {p}")
    return out


if __name__ == "__main__":
    main(emit_json="--json" in sys.argv)
