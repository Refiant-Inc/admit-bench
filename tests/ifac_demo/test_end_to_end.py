from admitbench.ifac_demo import CSTRDemoSession, DemoPhase, ExecutionGateway, HumanActionSelection


def _run(cstr):
    session = CSTRDemoSession(cartridge=cstr)
    session.show_consent()
    session.start(consent=False)
    session.advance(300)
    session.record_diagnosis("thermal runaway caused by cooling loss")
    session.perform_check("confirm_hazard_signal")
    result = ExecutionGateway().submit_human_action(
        session,
        HumanActionSelection(
            action="controlled_shutdown",
            cited_evidence=session.evidence_ids_for_tags("TT-101", "FT-201"),
            confidence=0.9,
        ),
    )
    return session, result


def test_full_deterministic_backend_demo_and_one_click_reset(cstr):
    first, first_result = _run(cstr)
    second, second_result = _run(cstr)

    assert first_result.score.verdict == "admissible"
    assert first_result.action_executed
    assert first.state["shutdown"] == 1.0
    assert first_result.pre_action_state == second_result.pre_action_state
    assert first_result.gate_report.to_dict() == second_result.gate_report.to_dict()

    action_temperature = first.state["T"]
    first.advance(300.0)
    assert first.phase == DemoPhase.OUTCOME
    assert first.state["T"] < action_temperature
    assert first.world.margin(first.state)[0] > 0.0

    first.show_summary()
    assert first.phase == DemoPhase.SESSION_SUMMARY
    first.new_participant()
    assert first.phase == DemoPhase.WELCOME
    assert first.state["shutdown"] == 0.0
