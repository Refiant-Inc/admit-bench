import csv
import json

import pytest

from admitbench.ifac_demo import CSTRDemoSession, DemoController, ResearchStore


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def complete_decision(cstr, tmp_path, consent):
    clock = FakeClock()
    store = ResearchStore(tmp_path / "ifac-data")
    controller = DemoController(
        session=CSTRDemoSession(cartridge=cstr),
        research_store=store,
        clock=clock,
    )
    controller.begin()
    controller.set_consent(consent)
    controller.advance(300.0)
    clock.value += 13.4
    controller.diagnose("Thermal runaway caused by cooling loss")
    controller.perform_check("verify_coolant_flow")
    controller.perform_check("check_valve_lineup")
    state = controller.snapshot()
    citations = [
        entry["id"]
        for entry in state["evidence"]
        if entry["tag"] in ("TT-101", "FT-201", "FC-201")
    ]
    result = controller.submit_action(
        {
            "action": "increase_coolant_flow",
            "params": {"delta_pct": 150},
            "cited_evidence": citations,
            "confidence": 0.9,
            "optional_reasoning": "My name and email must not be persisted here.",
        }
    )
    return store, controller, result


def test_declining_consent_persists_no_participant_record(cstr, tmp_path):
    store, controller, result = complete_decision(cstr, tmp_path, consent=False)
    assert result["decision"]["decision"] == "ADMISSIBLE"
    assert not result["research_recording"]
    assert store.records() == []
    assert not store.sessions_dir.exists()


def test_consenting_persists_minimal_deidentified_record(cstr, tmp_path):
    store, controller, result = complete_decision(cstr, tmp_path, consent=True)
    rows = store.records()
    assert result["research_recording"]
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {
        "session_id",
        "study_version",
        "timestamp",
        "scenario_id",
        "fault_type",
        "fault_time",
        "decision_index",
        "observations_visible_to_human",
        "human_action",
        "response_time_seconds",
        "admit_decision",
        "action_executed",
        "pre_action_state",
        "post_action_state",
        "outcome",
    }
    assert row["response_time_seconds"] == 13.4
    assert row["human_action"]["optional_reasoning"] is None
    assert row["human_action"]["action_type"] == "increase_coolant_flow"
    assert row["admit_decision"]["canonical_verdict"] == "admissible"
    serialized = json.dumps(row).lower()
    for key in ("email", "ip_address", "user_agent", "browser_fingerprint", "device_id"):
        assert f'"{key}"' not in serialized
    assert "my name and email" not in serialized


def test_research_store_rejects_forbidden_pii_fields(tmp_path):
    store = ResearchStore(tmp_path)
    session_id = store.begin_session(True)
    with pytest.raises(ValueError, match="forbidden identifying fields"):
        store.persist(session_id, {"network": {"ip_address": "127.0.0.1"}})


def test_jsonl_and_csv_exports_derive_from_consented_records(cstr, tmp_path):
    store, _, _ = complete_decision(cstr, tmp_path, consent=True)
    jsonl = store.export("jsonl")
    csv_path = store.export("csv")
    assert len(jsonl.read_text(encoding="utf-8").splitlines()) == 1
    with csv_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["action_type"] == "increase_coolant_flow"
    assert rows[0]["decision"] == "ADMISSIBLE"
    assert rows[0]["session_id"]


def test_aggregate_summary_uses_only_persisted_rows(cstr, tmp_path):
    store, _, _ = complete_decision(cstr, tmp_path, consent=True)
    summary = store.summary()
    assert summary["consented_sessions"] == 1
    assert summary["total_interventions"] == 1
    assert summary["admissibility_rate"] == 1.0
    assert summary["median_response_time_seconds"] == 13.4


def test_phase5_summary_derives_every_dashboard_series(tmp_path):
    store = ResearchStore(tmp_path)

    def add(scenario, decision, action, response, *, tier=None, code=None, executed=False, crossed=None):
        failed = [{"code": code, "gate": tier, "message": "test failure"}] if code else []
        store.persist(
            store.begin_session(True),
            {
                "scenario_id": scenario,
                "response_time_seconds": response,
                "human_action": {"action_type": action, "params": {}},
                "admit_decision": {
                    "decision": decision,
                    "failed_tier": tier,
                    "failed_checks": failed,
                },
                "action_executed": executed,
                "outcome": (
                    {"crossed": crossed, "min_margin": 0.4 if crossed is False else -0.1}
                    if crossed is not None else {}
                ),
            },
        )

    add("scenario-a", "ADMISSIBLE", "increase_coolant_flow", 4, executed=True, crossed=False)
    add(
        "scenario-a", "BLOCKED", "hold_and_monitor", 12,
        tier="T4", code="AAS-T4-UNSAFE-DELAY", crossed=True,
    )
    add("scenario-b", "ESCALATION_INITIATED", "escalate_to_operator", 25)
    add(
        "scenario-b", "UNRESOLVED_HOLD", "verify_sensor", 70,
        tier="T0", code="AAS-T0-NOT-EVALUABLE",
    )

    summary = store.summary()
    assert summary["consented_sessions"] == 4
    assert summary["total_interventions"] == 4
    assert summary["admissibility_rate"] == 0.5
    assert summary["escalated_interventions"] == 1
    assert summary["most_common_failed_tier"] in {"T0", "T4"}
    assert summary["failed_check_counts"] == {
        "AAS-T4-UNSAFE-DELAY": 1,
        "AAS-T0-NOT-EVALUABLE": 1,
    }
    assert summary["action_counts"] == {
        "increase_coolant_flow": 1,
        "hold_and_monitor": 1,
        "escalate_to_operator": 1,
        "verify_sensor": 1,
    }
    assert summary["scenario_metrics"]["scenario-a"]["admissibility_rate"] == 0.5
    assert summary["scenario_metrics"]["scenario-b"]["escalated"] == 1
    assert [bucket["count"] for bucket in summary["response_time_histogram"]] == [1, 0, 1, 1, 0, 1]
    assert summary["outcome_metrics"] == {
        "actions_executed": 1,
        "projected_safe": 1,
        "projected_crossing": 0,
        "mean_projected_min_margin": 0.4,
    }
