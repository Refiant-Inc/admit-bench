"""Thin composition over the canonical ADMIT gate chain."""

from __future__ import annotations

from admitbench.cartridge import Cartridge, Case
from admitbench.gates import GateReport, run_gates
from admitbench.physics import World
from admitbench.record import ActionRecord, DecisionContext, EvidenceLog
from admitbench.scoring import Score, score_episode


def evaluate_action(
    cartridge: Cartridge,
    case: Case,
    record: ActionRecord,
    evidence: EvidenceLog,
    context: DecisionContext,
    world: World,
) -> tuple[GateReport, dict, Score]:
    """Evaluate one human record without duplicating a governance predicate."""
    report, artifacts = run_gates(
        record,
        case,
        cartridge.rulebook,
        evidence,
        context,
        world,
        horizon_s=cartridge.horizon_s,
    )
    state = world.initial_state(case.initial_state)
    if case.oracle:
        artifacts["oracle_trajectory"] = world.project(
            state,
            case.oracle["action"],
            case.oracle.get("params"),
            horizon_s=cartridge.horizon_s,
        ).summary()
    score = score_episode(record, case, cartridge.rulebook, report, artifacts, cartridge.weights)
    return report, artifacts, score
