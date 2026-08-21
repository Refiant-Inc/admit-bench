"""Application controller joining the live session, gateway, and research store."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from admitbench.ifac_demo.gateway import ExecutionGateway
from admitbench.ifac_demo.models import DemoPhase, HumanActionSelection
from admitbench.ifac_demo.research import ResearchStore
from admitbench.ifac_demo.session import CSTRDemoSession, load_scenario


class DemoController:
    OUTCOME_WINDOW_S = 300.0

    def __init__(
        self,
        session: CSTRDemoSession | None = None,
        research_store: ResearchStore | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.session = session or CSTRDemoSession()
        self.research_store = research_store or ResearchStore()
        self.gateway = ExecutionGateway()
        self._clock = clock
        self._lock = threading.RLock()
        self._research_session_id = None
        self._decision_started_at = None
        self._decision_sim_time_s = None
        self._decision_index = 0
        self._submission_digests: dict[str, str] = {}

    def reset(self) -> dict:
        with self._lock:
            self.session.new_participant()
            self._research_session_id = None
            self._decision_started_at = None
            self._decision_sim_time_s = None
            self._decision_index = 0
            self._submission_digests.clear()
            return self.snapshot()

    def begin(self) -> dict:
        with self._lock:
            self.session.show_consent()
            return self.snapshot()

    def set_consent(self, consent: bool) -> dict:
        with self._lock:
            self._research_session_id = self.research_store.begin_session(bool(consent))
            self.session.start(consent=bool(consent))
            return self.snapshot()

    def advance(self, seconds: float) -> dict:
        with self._lock:
            before = self.session.phase
            self.session.advance(seconds)
            if before != DemoPhase.HUMAN_DIAGNOSIS and self.session.phase == DemoPhase.HUMAN_DIAGNOSIS:
                self._decision_started_at = self._clock()
            return self.snapshot()

    def diagnose(self, diagnosis: str) -> dict:
        with self._lock:
            self.session.record_diagnosis(diagnosis)
            return self.snapshot()

    def perform_check(self, check: str) -> dict:
        with self._lock:
            self.session.perform_check(check)
            return self.snapshot()

    def submit_action(self, raw: dict) -> dict:
        with self._lock:
            request_id = str(raw.get("request_id", "")).strip()
            digest = self._submission_digest(raw) if request_id else ""
            if request_id:
                prior = self._submission_digests.get(request_id)
                if prior is not None:
                    if prior != digest:
                        raise ValueError("request_id was already used for a different intervention")
                    return self.snapshot()

            observation = self.session.observation()
            selection = HumanActionSelection(
                action=str(raw.get("action", "")),
                params=dict(raw.get("params") or {}),
                cited_evidence=[str(value) for value in (raw.get("cited_evidence") or [])],
                confidence=float(raw.get("confidence", 0.0)),
                diagnosis=str(raw.get("diagnosis", "")),
                optional_reasoning=str(raw.get("optional_reasoning", "")),
            )
            started_at = self._decision_started_at
            now = self._clock()
            response_time = max(
                0.0,
                now - started_at if started_at is not None else 0.0,
            )
            decision = self.gateway.submit_human_action(self.session, selection)
            self._decision_index += 1
            self._decision_sim_time_s = self.session.sim_time_s
            # The action may already have reached the live process.  Cache the
            # idempotency key before best-effort research persistence so that a
            # storage failure can never make a network retry execute it twice.
            if request_id:
                self._submission_digests[request_id] = digest
            self._persist_decision(observation, decision, response_time)
            return self.snapshot()

    def show_summary(self) -> dict:
        with self._lock:
            decision = self.session._last_decision
            if (
                decision is not None
                and decision.reaches_plant
                and self._decision_sim_time_s is not None
                and self.session.sim_time_s - self._decision_sim_time_s < self.OUTCOME_WINDOW_S
            ):
                raise RuntimeError("the measured outcome window is still running")
            self.session.show_summary()
            return self.snapshot()

    def actions(self) -> list[dict]:
        names = self.session.scenario.participant_actions or tuple(self.session.cartridge.rulebook.actions())
        result = []
        for name in names:
            rule = self.session.cartridge.rulebook.get(name)
            result.append(
                {
                    "action": rule.action,
                    "description": rule.description,
                    "action_class": rule.action_class,
                    "required_params": list(rule.required_params),
                    "required_steps": list(rule.required_steps),
                    "required_evidence_tags": list(rule.required_evidence_tags),
                    "reversibility": rule.reversibility,
                }
            )
        return result

    def snapshot(self) -> dict:
        phase = self.session.phase
        show_evidence = phase in (
            DemoPhase.HUMAN_DIAGNOSIS,
            DemoPhase.HUMAN_ACTION_SELECTION,
            DemoPhase.ADMIT_EVALUATION,
            DemoPhase.OUTCOME,
            DemoPhase.SESSION_SUMMARY,
        )
        decision = self.session._last_decision
        return {
            "phase": phase.value,
            "scenario_id": self.session.scenario.id,
            "scenario_label": "CSTR disturbance response",
            "simulation_time_s": round(self.session.sim_time_s, 3),
            "state": self.session.state,
            "history": self.session.history,
            "safe_limits": self.session.world.envelope(),
            "operating_mode": self.session.scenario.mode,
            "fault_status": "DETECTED" if self.session.fault_injected else "NONE",
            "fault_time_s": self.session.fault_injected_at_s if self.session.fault_injected else None,
            "evidence": self.session.visible_evidence() if show_evidence else [],
            "verified_checks": self.session.verified_checks,
            "diagnosis_options": list(self.session.scenario.diagnosis_options),
            "actions": self.actions() if phase == DemoPhase.HUMAN_ACTION_SELECTION else [],
            "research_recording": self._research_session_id is not None,
            "decision": decision.to_dict(include_debug=True) if decision else None,
            "outcome_summary": self.outcome_summary(),
            "outcome_window_s": self.OUTCOME_WINDOW_S,
            "outcome_elapsed_s": (
                round(self.session.sim_time_s - self._decision_sim_time_s, 3)
                if self._decision_sim_time_s is not None
                else 0.0
            ),
        }

    def outcome_summary(self) -> dict | None:
        """Summarize measured live evolution after the governance decision."""
        decision = self.session._last_decision
        if decision is None or self._decision_sim_time_s is None:
            return None

        envelope = self.session.world.envelope().get("T", {})
        trip_limit = float(envelope.get("limit", 370.0))
        nominal = float(envelope.get("nominal", 350.0))
        recovery_limit = nominal + 2.0
        samples = [
            row
            for row in self.session.history
            if float(row.get("time_s", -1.0)) >= self._decision_sim_time_s
        ]
        if not samples:
            samples = [{"time_s": self.session.sim_time_s, **self.session.state}]
        temperatures = [float(row["T"]) for row in samples if "T" in row]
        margins = [trip_limit - value for value in temperatures]
        recovery_time = next(
            (
                float(row["time_s"]) - self._decision_sim_time_s
                for row in samples
                if float(row.get("T", float("inf"))) <= recovery_limit
            ),
            None,
        )

        if not decision.reaches_plant:
            disposition = "PAUSED_NO_EXECUTION"
        elif decision.action_executed:
            disposition = "AUTHORIZED_ACTION_APPLIED"
        else:
            disposition = "AUTHORIZED_NONCOMMITTING_ACTION"

        return {
            "disposition": disposition,
            "authoritative_state_paused": not decision.reaches_plant,
            "action": decision.record.action,
            "action_executed": bool(decision.action_executed),
            "maximum_temperature_k": round(max(temperatures), 3),
            "current_temperature_k": round(float(self.session.state["T"]), 3),
            "minimum_margin_k": round(min(margins), 3),
            "current_margin_k": round(trip_limit - float(self.session.state["T"]), 3),
            "trip_boundary_crossed": any(value >= trip_limit for value in temperatures),
            "recovered_to_nominal_band": recovery_time is not None,
            "recovery_band_k": round(recovery_limit, 3),
            "recovery_time_seconds": round(recovery_time, 3) if recovery_time is not None else None,
        }

    @staticmethod
    def _submission_digest(raw: dict) -> str:
        payload = {key: value for key, value in raw.items() if key != "request_id"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _persist_decision(self, observation, decision, response_time: float) -> None:
        if self._research_session_id is None:
            return
        gate_rows = decision.gate_report.to_dict()["results"]
        failed = [violation for gate in gate_rows for violation in gate["violations"]]
        action_artifact = (
            decision.artifacts.get("action_trajectory")
            or decision.artifacts.get("response_window_trajectory")
            or {}
        )
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scenario_id": self.session.scenario.id,
            "fault_type": "+".join(sorted(self.session.scenario.fault_overrides)) + "_override",
            "fault_time": self.session.fault_injected_at_s,
            "decision_index": self._decision_index,
            "observations_visible_to_human": {
                "simulation_time_s": observation.sim_time_s,
                "state": observation.state,
                "evidence": observation.evidence,
                "operating_mode": observation.mode,
            },
            "human_action": {
                "action_type": decision.record.action,
                "params": decision.record.params,
                # Free-text reasoning is deliberately not persisted: it is not
                # needed for the minimum study questions and may contain PII.
                "optional_reasoning": None,
            },
            "response_time_seconds": round(response_time, 3),
            "admit_decision": {
                "decision": decision.display_decision,
                "canonical_verdict": decision.score.verdict,
                "failed_tier": decision.gate_report.first_failing,
                "aas_code": decision.gate_report.aas_code,
                "failed_checks": failed,
                "gates": [
                    {"tier": gate["tier"], "name": gate["name"], "passed": gate["passed"]}
                    for gate in gate_rows
                ],
            },
            "action_executed": decision.action_executed,
            "pre_action_state": decision.pre_action_state,
            "post_action_state": decision.post_action_state,
            "outcome": action_artifact,
        }
        self.research_store.persist(self._research_session_id, payload)
