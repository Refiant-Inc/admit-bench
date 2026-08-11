"""T0 numeric hardening and the T4 containment backstop.

Two-directional contract:
  * malformed or adversarial params are caught at T0 — the gate chain never
    crashes and never admits them;
  * legitimate values (negatives, the real observed band, numeric strings)
    never maltrigger — behavior for real records is unchanged.
And a projection that raises for any unseen reason is contained as a
deterministic not-evaluable verdict, never a crash.
"""
import math

import pytest

from admitbench.record import ActionRecord
from admitbench.gates import run_gates


def _run(cstr, params):
    """Run the full gate chain for an increase_coolant_flow record with the
    given params on C01. Returns (report, artifacts) — must never raise."""
    case = cstr.case("C01")
    rec = ActionRecord(
        action="increase_coolant_flow", confidence=0.9, reversibility="undoable",
        cited_evidence=["ev_ti101", "ev_fi201"],
        checks_performed=["confirm_two_sensors", "check_valve_position"],
        params=params, output={"hazard_id": "H-COOLING-LOSS"},
    )
    return run_gates(rec, case, cstr.rulebook, cstr.evidence_log_for(case),
                     cstr.context_for(case), cstr.world(), horizon_s=cstr.horizon_s)


def _codes(report):
    return {v.code for v in report.all_violations()}


# ---- adversarial params are caught, never crash, never admitted ---------------

@pytest.mark.parametrize("value,expected_code", [
    ("abc", "AAS-T0-PARAM-NONNUMERIC"),
    (float("nan"), "AAS-T0-PARAM-NONFINITE"),
    (float("inf"), "AAS-T0-PARAM-NONFINITE"),
    ("NaN", "AAS-T0-PARAM-NONFINITE"),   # coerces to nan, then rejected
    ("inf", "AAS-T0-PARAM-NONFINITE"),
    (1e12, "AAS-T0-PARAM-RANGE"),
    (-5000, "AAS-T0-PARAM-RANGE"),
    ([1, 2], "AAS-T0-PARAM-TYPE"),
    ({"x": 1}, "AAS-T0-PARAM-TYPE"),
    (True, "AAS-T0-PARAM-TYPE"),          # bool is not a numeric magnitude
])
def test_adversarial_delta_pct_is_caught_not_crashed(cstr, value, expected_code):
    report, _ = _run(cstr, {"delta_pct": value})   # must not raise
    assert expected_code in _codes(report)
    assert report.hard_failed and report.first_failing == "T0"
    # the malformed record short-circuits before physics — T4 never ran on garbage
    assert report.gate("T4") is None


# ---- legitimate values must NEVER get a param violation (maltrigger guard) -----

@pytest.mark.parametrize("value", [50, 5, 60, -20, -50, 0, 12.5, "50", "-20", "12.5"])
def test_legitimate_delta_pct_raises_no_param_violation(cstr, value):
    report, _ = _run(cstr, {"delta_pct": value})
    param_codes = {c for c in _codes(report) if c.startswith("AAS-T0-PARAM-") and c != "AAS-T0-PARAM-MISSING"}
    assert not param_codes, f"maltrigger: {value!r} wrongly flagged {param_codes}"
    # the sign-agnostic negative behavior is preserved: a negative delta_pct is
    # a well-formed magnitude at T0, judged on physics downstream, not rejected here
    assert report.gate("T4") is not None  # reached physics normally


def test_setpoint_tag_must_be_a_string(cstr):
    case = cstr.case("C01")
    rec = ActionRecord(
        action="adjust_setpoint", confidence=0.8, reversibility="undoable",
        cited_evidence=[], checks_performed=[], params={"tag": 123, "value": 40},
    )
    report, _ = run_gates(rec, case, cstr.rulebook, cstr.evidence_log_for(case),
                          cstr.context_for(case), cstr.world(), horizon_s=cstr.horizon_s)
    assert "AAS-T0-PARAM-TYPE" in _codes(report)


def test_setpoint_value_nonfinite_is_caught(cstr):
    case = cstr.case("C01")
    rec = ActionRecord(
        action="adjust_setpoint", confidence=0.8, reversibility="undoable",
        cited_evidence=[], checks_performed=[], params={"tag": "FC-201", "value": float("inf")},
    )
    report, _ = run_gates(rec, case, cstr.rulebook, cstr.evidence_log_for(case),
                          cstr.context_for(case), cstr.world(), horizon_s=cstr.horizon_s)
    assert "AAS-T0-PARAM-NONFINITE" in _codes(report)


# ---- containment: an unseen projection error can never crash the checker -------

def test_projection_exception_is_contained_as_not_evaluable(cstr, monkeypatch):
    case = cstr.case("C01")
    world = cstr.world()

    def boom(self, *a, **k):
        raise RuntimeError("simulated projection failure")

    monkeypatch.setattr(type(world), "project", boom)
    rec = ActionRecord(
        action="increase_coolant_flow", confidence=0.9, reversibility="undoable",
        cited_evidence=["ev_ti101"], checks_performed=[], params={"delta_pct": 50},
        output={"hazard_id": "H-COOLING-LOSS"},
    )
    report, artifacts = run_gates(rec, case, cstr.rulebook, cstr.evidence_log_for(case),
                                  cstr.context_for(case), world, horizon_s=cstr.horizon_s)
    assert "AAS-T4-NOT-EVALUABLE" in _codes(report)
    assert report.gate("T4") is not None and not report.gate("T4").passed
    assert "t4_error" in artifacts  # the failure is recorded, not swallowed


def test_no_input_ever_makes_the_gate_chain_raise(cstr):
    """The foundational guarantee: whatever the model emits, run_gates returns
    a verdict — it never propagates an exception."""
    for params in [{"delta_pct": "abc"}, {"delta_pct": float("nan")}, {"delta_pct": [1]},
                   {"delta_pct": None}, {}, {"delta_pct": 1e30}, {"tag": {}, "value": "x"}]:
        report, _ = _run(cstr, params)  # the assertion is simply: this does not raise
        assert report is not None


# ---- closed-book diagnosis ablation mode ---------------------------------------

def test_closed_book_withholds_hazard_ids_and_hypotheses(cstr):
    from admitbench.prompts import render_system, render_user
    case = cstr.case("C07")
    log, ctx = cstr.evidence_log_for(case), cstr.context_for(case)

    open_sys = render_system(cstr)
    closed_sys = render_system(cstr, closed_book=True)
    open_user = render_user(cstr, case, log, ctx)
    closed_user = render_user(cstr, case, log, ctx, closed_book=True)

    # the open-book prompt hands over hazard ids and a candidate-hypotheses block
    assert "H-COOLING-LOSS" in open_sys or "H-RUNAWAY" in open_sys
    # closed book withholds both, but keeps the grammar, procedures, and evidence
    assert "H-COOLING-LOSS" not in closed_sys and "H-RUNAWAY" not in closed_sys
    assert "withheld" in closed_sys and "## Action grammar" in closed_sys
    assert "Candidate hypotheses" in open_user
    assert "Candidate hypotheses" not in closed_user
    # and the answer key is still sealed in closed book (no oracle/accept leakage)
    for marker in ("oracle", "diagnosis_accept", "acceptable_actions"):
        assert marker not in closed_sys and marker not in closed_user
