from html.parser import HTMLParser
from importlib.resources import files

import pytest
from fastapi.testclient import TestClient

from admitbench.ifac_demo import CSTRDemoSession, DemoController, ResearchStore
from admitbench.ifac_demo.server import create_app


def _client_at_decision(cstr, tmp_path, *, consent=True):
    controller = DemoController(
        session=CSTRDemoSession(cartridge=cstr),
        research_store=ResearchStore(tmp_path / "records"),
    )
    client = TestClient(create_app(controller))
    client.post("/api/session/begin")
    client.post("/api/session/consent", json={"consent": consent})
    client.post("/api/session/advance", json={"seconds": 300})
    client.post(
        "/api/session/diagnosis",
        json={"diagnosis": "Thermal runaway caused by cooling loss"},
    )
    return client, controller


def _coolant_proposal(client, request_id="conference-click-1"):
    client.post("/api/session/check", json={"check": "verify_coolant_flow"})
    state = client.post("/api/session/check", json={"check": "check_valve_lineup"}).json()
    citations = [
        entry["id"]
        for entry in state["evidence"]
        if entry["tag"] in ("TT-101", "FT-201", "FC-201")
    ]
    return {
        "request_id": request_id,
        "action": "increase_coolant_flow",
        "params": {"delta_pct": 150},
        "cited_evidence": citations,
        "confidence": 0.9,
    }


def test_retried_request_is_idempotent_across_the_execution_and_research_boundaries(
    cstr, tmp_path
):
    client, controller = _client_at_decision(cstr, tmp_path)
    proposal = _coolant_proposal(client)

    first = client.post("/api/session/action", json=proposal)
    retry = client.post("/api/session/action", json=proposal)

    assert first.status_code == retry.status_code == 200
    assert retry.json()["decision"] == first.json()["decision"]
    assert controller.session.live_execution_count == 1
    assert len(controller.research_store.records()) == 1


def test_reusing_an_idempotency_key_for_a_different_proposal_is_rejected(cstr, tmp_path):
    client, controller = _client_at_decision(cstr, tmp_path)
    proposal = _coolant_proposal(client)
    assert client.post("/api/session/action", json=proposal).status_code == 200

    changed = {**proposal, "params": {"delta_pct": 100}}
    response = client.post("/api/session/action", json=changed)

    assert response.status_code == 409
    assert "different intervention" in response.json()["detail"]
    assert controller.session.live_execution_count == 1


def test_browser_refresh_recovers_the_authoritative_server_state(cstr, tmp_path):
    client, _ = _client_at_decision(cstr, tmp_path, consent=False)
    proposal = _coolant_proposal(client, request_id="refresh-case")
    submitted = client.post("/api/session/action", json=proposal).json()

    refreshed = client.get("/api/session").json()

    assert refreshed["phase"] == "OUTCOME"
    assert refreshed["decision"] == submitted["decision"]
    assert refreshed["state"] == submitted["state"]


def test_live_outcome_summary_is_measured_from_post_decision_history(cstr, tmp_path):
    client, _ = _client_at_decision(cstr, tmp_path, consent=False)
    state = client.get("/api/session").json()
    citations = [
        entry["id"] for entry in state["evidence"] if entry["tag"] in ("TT-101", "FT-201")
    ]
    client.post("/api/session/check", json={"check": "confirm_hazard_signal"})
    submitted = client.post(
        "/api/session/action",
        json={
            "request_id": "measured-recovery",
            "action": "controlled_shutdown",
            "params": {},
            "cited_evidence": citations,
            "confidence": 0.9,
        },
    ).json()
    assert submitted["outcome_summary"]["recovered_to_nominal_band"] is False
    assert client.post("/api/session/summary").status_code == 409

    outcome = client.post("/api/session/advance", json={"seconds": 300}).json()[
        "outcome_summary"
    ]

    assert outcome["disposition"] == "AUTHORIZED_ACTION_APPLIED"
    assert outcome["action_executed"] is True
    assert outcome["trip_boundary_crossed"] is False
    assert outcome["recovered_to_nominal_band"] is True
    assert outcome["recovery_time_seconds"] == pytest.approx(105.0)
    assert outcome["maximum_temperature_k"] == pytest.approx(358.65, abs=0.01)
    assert outcome["current_margin_k"] > 40.0


def test_blocked_outcome_is_explicitly_paused_without_an_invented_fallback(cstr, tmp_path):
    client, controller = _client_at_decision(cstr, tmp_path, consent=False)
    before = client.get("/api/session").json()["state"]
    response = client.post(
        "/api/session/action",
        json={
            "request_id": "blocked-hold",
            "action": "hold_and_monitor",
            "params": {},
            "cited_evidence": [],
            "confidence": 0.8,
        },
    ).json()

    assert response["decision"]["reaches_plant"] is False
    assert response["state"] == before
    assert response["outcome_summary"]["disposition"] == "PAUSED_NO_EXECUTION"
    assert response["outcome_summary"]["authoritative_state_paused"] is True
    assert controller.session.live_execution_count == 0
    assert client.post("/api/session/advance", json={"seconds": 1}).status_code == 409


class _ParticipantMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.html_attrs = {}
        self.ids = set()
        self.canvases = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "html":
            self.html_attrs = attributes
        if "id" in attributes:
            self.ids.add(attributes["id"])
        if tag == "canvas":
            self.canvases.append(attributes)


def test_participant_page_has_offline_accessibility_and_reconnect_contracts():
    root = files("admitbench.ifac_demo")
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "static" / "app.js").read_text(encoding="utf-8")
    css = (root / "static" / "styles.css").read_text(encoding="utf-8")
    parser = _ParticipantMarkup()
    parser.feed(html)

    assert parser.html_attrs["lang"] == "en"
    assert {"connection-pill", "fullscreen", "interaction", "toast"} <= parser.ids
    assert len(parser.canvases) == 3
    assert all(canvas.get("role") == "img" and canvas.get("aria-label") for canvas in parser.canvases)
    assert "http://" not in html and "https://" not in html
    assert "request_id" in javascript and "randomUUID" in javascript
    assert "setInterval(()=>{if(!snapshot" in javascript
    assert "prefers-reduced-motion" in css and ":focus-visible" in css


def test_wheel_package_resource_contract_includes_every_demo_asset():
    root = files("admitbench.ifac_demo")
    required = (
        root / "scenarios" / "conference_cstr.yaml",
        root / "static" / "index.html",
        root / "static" / "styles.css",
        root / "static" / "app.js",
        root / "static" / "research.html",
    )
    assert all(resource.is_file() for resource in required)
