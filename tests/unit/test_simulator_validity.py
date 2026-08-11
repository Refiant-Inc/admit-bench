"""Numerical validity of the benchmark worlds — a regression guard on the
production integration step (Finding 07)."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("sv", REPO / "scripts" / "simulator_validity.py")
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)


def test_production_dt_is_converged():
    """dt=3s must not flip any verdict vs a 16x finer reference, and crossing
    times must move <2% — numerical error is not a source of verdict noise."""
    for name, world in (("cstr", "cstr"), ("distillation", "column")):
        _, cases = sv._cases(name)
        conv = sv.convergence(name, world, cases)
        assert conv["verdict_flips_prod_vs_reference"] == 0, name
        assert conv["worst_crossed_at_deviation_pct"] < 2.0, name


def test_actuator_lag_is_reported():
    _, cases = sv._cases("cstr")
    lag = sv.actuator_lag("cstr", "cstr", cases)
    assert "cases_with_lag_sensitive_verdict" in lag and lag["lags_s"][0] == 0
