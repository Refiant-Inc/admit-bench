import pytest

from admitbench.ifac_demo import HumanActionProposer, HumanActionSelection


def test_human_selection_maps_to_canonical_action_record(decision_session):
    session = decision_session
    session.perform_check("verify_coolant_flow")
    session.perform_check("check_valve_lineup")
    citations = session.evidence_ids_for_tags("TT-101", "FT-201", "FC-201")
    selection = HumanActionSelection(
        action="increase_coolant_flow",
        params={"delta_pct": 150},
        cited_evidence=citations,
        confidence=0.9,
        optional_reasoning="Cooling is low while temperature is accelerating.",
    )
    record = HumanActionProposer(
        selection,
        session.cartridge.rulebook,
        session.cartridge.recovery_for,
    ).propose(session.observation())

    assert record.action == "increase_coolant_flow"
    assert record.params == {"delta_pct": 150}
    assert record.output == {"diagnosis": "thermal runaway caused by cooling loss"}
    assert record.cited_evidence == citations
    assert record.checks_performed == ["verify_coolant_flow", "check_valve_lineup"]
    assert record.reversibility == "undoable"
    assert "restore FC-201" in record.recovery_plan
    assert record.retry_key is None


def test_design_time_reversibility_and_retry_key_do_not_come_from_human(decision_session):
    session = decision_session
    session.perform_check("confirm_hazard_signal")
    selection = HumanActionSelection(action="controlled_shutdown", confidence=0.9)
    record = HumanActionProposer(
        selection,
        session.cartridge.rulebook,
        session.cartridge.recovery_for,
        retry_key_factory=lambda: "server-key",
    ).propose(session.observation())
    assert record.reversibility == "costly_to_undo"
    assert record.retry_key == "server-key"
    assert "restart" in record.recovery_plan.lower()


def test_human_cannot_cite_evidence_the_server_did_not_show(decision_session):
    proposer = HumanActionProposer(
        HumanActionSelection(action="hold_and_monitor", cited_evidence=["invented"]),
        decision_session.cartridge.rulebook,
        decision_session.cartridge.recovery_for,
    )
    with pytest.raises(ValueError, match="was not visible"):
        proposer.propose(decision_session.observation())
