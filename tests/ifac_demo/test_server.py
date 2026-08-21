import pytest
from fastapi.testclient import TestClient

from admitbench.ifac_demo import CSTRDemoSession, DemoController, ResearchStore
from admitbench.ifac_demo.server import create_app, validate_deployment


def client_for(cstr, tmp_path):
    controller = DemoController(
        session=CSTRDemoSession(cartridge=cstr),
        research_store=ResearchStore(tmp_path / "data"),
    )
    return TestClient(create_app(controller)), controller


def test_participant_and_research_pages_are_self_contained_local_assets(cstr, tmp_path):
    client, _ = client_for(cstr, tmp_path)
    participant = client.get("/")
    research = client.get("/research")
    javascript = client.get("/static/app.js")
    assert participant.status_code == research.status_code == javascript.status_code == 200
    assert "Continuous stirred-tank reactor" in participant.text
    assert "Conference decision signals" in research.text
    assert "decision-donut" in research.text
    assert "failure-bars" in research.text
    assert "action-bars" in research.text
    assert "response-histogram" in research.text
    assert "scenario-table" in research.text
    assert "/api/research/records" not in research.text
    assert "/api/session/action" in javascript.text
    assert "http://" not in participant.text and "https://" not in participant.text


def test_http_flow_reaches_gateway_and_returns_real_gate_results(cstr, tmp_path):
    client, controller = client_for(cstr, tmp_path)
    assert client.get("/api/session").json()["phase"] == "WELCOME"
    assert client.post("/api/session/begin").json()["phase"] == "CONSENT"
    assert client.post("/api/session/consent", json={"consent": False}).json()["phase"] == "NOMINAL_OPERATION"
    state = client.post("/api/session/advance", json={"seconds": 300}).json()
    assert state["phase"] == "HUMAN_DIAGNOSIS"
    state = client.post(
        "/api/session/diagnosis",
        json={"diagnosis": "Thermal runaway caused by cooling loss"},
    ).json()
    assert state["phase"] == "HUMAN_ACTION_SELECTION" and len(state["actions"]) == 5
    client.post("/api/session/check", json={"check": "verify_coolant_flow"})
    state = client.post("/api/session/check", json={"check": "check_valve_lineup"}).json()
    citations = [
        entry["id"]
        for entry in state["evidence"]
        if entry["tag"] in ("TT-101", "FT-201", "FC-201")
    ]
    response = client.post(
        "/api/session/action",
        json={
            "action": "increase_coolant_flow",
            "params": {"delta_pct": 150},
            "cited_evidence": citations,
            "confidence": 0.9,
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["decision"]["decision"] == "ADMISSIBLE"
    assert result["decision"]["action_executed"]
    assert result["state"]["coolant_flow"] == 100.0
    assert [gate["tier"] for gate in result["decision"]["gates"]["results"]] == [
        "T0", "T1", "T2", "T3", "T4", "T6"
    ]
    assert controller.session.live_execution_count == 1


def test_invalid_state_transition_is_a_conflict_not_a_server_error(cstr, tmp_path):
    client, _ = client_for(cstr, tmp_path)
    response = client.post("/api/session/action", json={"action": "hold_and_monitor"})
    assert response.status_code == 409
    assert "no action observation" in response.json()["detail"]


def test_research_api_honors_optional_admin_token(cstr, tmp_path, monkeypatch):
    client, _ = client_for(cstr, tmp_path)
    monkeypatch.setenv("ADMIT_IFAC_ADMIN_TOKEN", "conference-secret")
    assert client.get("/api/research/summary").status_code == 403
    response = client.get(
        "/api/research/summary",
        headers={"X-ADMIT-Admin-Token": "conference-secret"},
    )
    assert response.status_code == 200
    assert response.json()["total_interventions"] == 0


def test_reset_endpoint_restores_welcome_without_restarting_server(cstr, tmp_path):
    client, _ = client_for(cstr, tmp_path)
    client.post("/api/session/begin")
    client.post("/api/session/consent", json={"consent": False})
    reset = client.post("/api/session/new").json()
    assert reset["phase"] == "WELCOME"
    assert reset["simulation_time_s"] == 0.0
    assert reset["state"]["T"] == 350.0


def test_remote_bind_requires_admin_token(monkeypatch):
    monkeypatch.delenv("ADMIT_IFAC_ADMIN_TOKEN", raising=False)
    validate_deployment("127.0.0.1")
    with pytest.raises(RuntimeError, match="requires ADMIT_IFAC_ADMIN_TOKEN"):
        validate_deployment("0.0.0.0")
    monkeypatch.setenv("ADMIT_IFAC_ADMIN_TOKEN", "secret")
    validate_deployment("0.0.0.0")
