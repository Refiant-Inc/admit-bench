import pytest

from admitbench.ifac_demo import CSTRDemoSession, DemoPhase, load_scenario


def test_default_scenario_is_deterministic_and_uses_c07():
    scenario = load_scenario()
    assert scenario.source_case_id == "C07"
    assert scenario.fault_time_s == 30.0
    assert scenario.fault_overrides == {"coolant_flow": 40.0}
    assert scenario.seed == 0


def test_nominal_run_fault_and_decision_use_real_cstr_state(cstr):
    session = CSTRDemoSession(cartridge=cstr)
    session.start()

    session.advance(29.0)
    assert session.phase == DemoPhase.NOMINAL_OPERATION
    assert not session.fault_injected
    assert session.state["coolant_flow"] == 100.0

    session.advance(1.0)
    assert session.phase == DemoPhase.FAULT_INTRODUCED
    assert session.fault_injected_at_s == 30.0
    assert session.state["coolant_flow"] == 40.0

    session.advance(300.0)
    assert session.phase == DemoPhase.HUMAN_DIAGNOSIS
    assert session.sim_time_s == pytest.approx(153.0)
    assert session.state["T"] >= 358.0
    assert session.state["T"] < session.world.T_TRIP
    assert session.state["Ca"] != 0.25


def test_visible_measurements_are_admitted_from_current_state(decision_session):
    session = decision_session
    evidence = {entry["tag"]: entry for entry in session.visible_evidence()}
    assert evidence["TT-101"]["value"] == session.state["T"]
    assert evidence["FT-201"]["value"] == session.state["coolant_flow"]
    assert evidence["CA-101"]["value"] == session.state["Ca"]
    assert evidence["TT-101"]["trust"] == "trusted"


def test_only_supported_checks_can_be_recorded(decision_session):
    session = decision_session
    session.perform_check("verify_coolant_flow")
    session.perform_check("verify_coolant_flow")
    assert session.verified_checks == ["verify_coolant_flow"]
    with pytest.raises(ValueError, match="unsupported live check"):
        session.perform_check("pretend_the_valve_was_checked")


def test_reset_restores_a_clean_new_participant_session(decision_session):
    session = decision_session
    session.perform_check("confirm_hazard_signal")
    session.new_participant()
    assert session.phase == DemoPhase.WELCOME
    assert session.sim_time_s == 0.0
    assert session.state == session.world.initial_state(session.scenario.initial_state)
    assert not session.fault_injected
    assert session.verified_checks == []
    assert session.live_execution_count == 0
    assert len(session.history) == 1


def test_plant_clock_is_paused_during_human_decision(decision_session):
    with pytest.raises(RuntimeError, match="plant clock is paused"):
        decision_session.advance(3.0)
