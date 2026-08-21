import pytest

from admitbench.ifac_demo import CSTRDemoSession, DemoPhase


@pytest.fixture
def decision_session(cstr):
    session = CSTRDemoSession(cartridge=cstr)
    session.show_consent()
    session.start(consent=False)
    session.advance(300.0)
    assert session.phase == DemoPhase.HUMAN_DIAGNOSIS
    session.record_diagnosis("thermal runaway caused by cooling loss")
    return session
