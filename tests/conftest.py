"""Shared fixtures.

Two test layers by design:
  tests/base    invariants every episode must satisfy — the benchmark's own contract
  tests/stress  metamorphic tests — does admissibility survive degradation and gaming?
  tests/unit    line-level coverage of each module
"""

from __future__ import annotations

from pathlib import Path

import pytest

from admitbench.cartridge import load_cartridge
from admitbench.providers import get_provider
from admitbench.record import ActionRecord

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def cstr():
    return load_cartridge(REPO / "admitbench" / "cartridges" / "cstr")


@pytest.fixture(scope="session")
def column():
    return load_cartridge(REPO / "admitbench" / "cartridges" / "distillation")


@pytest.fixture
def oracle():
    return get_provider("stub", "oracle")


@pytest.fixture
def reckless():
    return get_provider("stub", "reckless")


@pytest.fixture
def timid():
    return get_provider("stub", "timid")


@pytest.fixture
def silent():
    return get_provider("stub", "silent")


def make_record(**overrides) -> ActionRecord:
    """A record that is admissible on cstr C01 unless a field is overridden."""
    base = dict(
        action="increase_coolant_flow",
        params={"delta_pct": 50},
        output={"diagnosis": "cooling loss"},
        cited_evidence=["ev_tt101", "ev_ft201", "ev_valve"],
        checks_performed=["verify_coolant_flow", "check_valve_lineup"],
        confidence=0.9,
        reversibility="undoable",
        recovery_plan="restore FC-201 to the previous setpoint",
    )
    base.update(overrides)
    return ActionRecord(**base)


@pytest.fixture
def c01(cstr):
    case = cstr.case("C01")
    return case, cstr.evidence_log_for(case), cstr.context_for(case)
