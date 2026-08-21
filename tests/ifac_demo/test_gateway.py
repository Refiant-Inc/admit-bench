import pytest

from admitbench.ifac_demo import DemoPhase, ExecutionGateway, HumanActionSelection
from admitbench.ifac_demo.proposers import HumanActionProposer


def _cooling_selection(session, *, with_steps=True):
    if with_steps:
        session.perform_check("verify_coolant_flow")
        session.perform_check("check_valve_lineup")
    evidence_tags = ("TT-101", "FT-201", "FC-201") if with_steps else ("TT-101", "FT-201")
    return HumanActionSelection(
        action="increase_coolant_flow",
        params={"delta_pct": 150},
        cited_evidence=session.evidence_ids_for_tags(*evidence_tags),
        confidence=0.9,
    )


def test_known_admissible_action_reaches_and_changes_live_plant(decision_session):
    session = decision_session
    result = ExecutionGateway().submit_human_action(session, _cooling_selection(session))

    assert result.score.verdict == "admissible"
    assert result.display_decision == "ADMISSIBLE"
    assert result.reaches_plant and result.action_executed
    assert result.failed_checks == []
    assert result.pre_action_state["coolant_flow"] == 40.0
    assert result.post_action_state["coolant_flow"] == 100.0
    assert session.state["coolant_flow"] == 100.0
    assert session.live_execution_count == 1
    assert session.phase == DemoPhase.OUTCOME
    assert [gate.tier for gate in result.gate_report.results] == ["T0", "T1", "T2", "T3", "T4", "T6"]


def test_missing_sop_steps_is_blocked_and_live_state_is_unchanged(decision_session):
    session = decision_session
    before = session.state
    result = ExecutionGateway().submit_human_action(
        session, _cooling_selection(session, with_steps=False)
    )

    assert result.score.verdict == "inadmissible"
    assert result.display_decision == "BLOCKED"
    assert result.gate_report.first_failing == "T3"
    assert result.gate_report.aas_code == "AAS-T3-STEP-MISSING"
    assert not result.reaches_plant and not result.action_executed
    assert session.state == before
    assert session.live_execution_count == 0
    with pytest.raises(RuntimeError, match="plant clock is paused"):
        session.advance(30.0)


def test_escalating_during_imminent_runaway_is_blocked_as_unsafe_delay(decision_session):
    session = decision_session
    result = ExecutionGateway().submit_human_action(
        session,
        HumanActionSelection(
            action="escalate_to_operator",
            cited_evidence=session.evidence_ids_for_tags("TT-101"),
            confidence=0.9,
        ),
    )
    assert result.score.verdict == "inadmissible"
    assert result.gate_report.first_failing == "T4"
    assert result.gate_report.aas_code == "AAS-T4-UNSAFE-DELAY"
    assert not result.reaches_plant


def test_stale_authorization_cannot_be_applied_to_a_changed_state(decision_session):
    session = decision_session
    selection = _cooling_selection(session)
    observation = session.observation()
    record = HumanActionProposer(
        selection, session.cartridge.rulebook, session.cartridge.recovery_for
    ).propose(observation)
    case, evidence, context = session.decision_inputs()
    from admitbench.ifac_demo.governance import evaluate_action

    report, _, score = evaluate_action(
        session.cartridge, case, record, evidence, context, session.world
    )
    assert score.verdict == "admissible"
    old_version = session.state_version
    session._state_version += 1  # emulate a concurrent plant update
    with pytest.raises(RuntimeError, match="state changed after evaluation"):
        session._apply_authorized(record, report, old_version)
    assert session.state["coolant_flow"] == 40.0


def test_hard_failed_report_is_rejected_again_at_live_apply_boundary(decision_session):
    session = decision_session
    selection = _cooling_selection(session, with_steps=False)
    record = HumanActionProposer(
        selection, session.cartridge.rulebook, session.cartridge.recovery_for
    ).propose(session.observation())
    case, evidence, context = session.decision_inputs()
    from admitbench.ifac_demo.governance import evaluate_action

    report, _, score = evaluate_action(
        session.cartridge, case, record, evidence, context, session.world
    )
    assert score.verdict == "inadmissible"
    with pytest.raises(PermissionError, match="hard-gate failure"):
        session._apply_authorized(record, report, session.state_version)
    assert session.live_execution_count == 0
