"""Data contracts for the IFAC backend integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from admitbench.gates import GateReport
from admitbench.record import ActionRecord
from admitbench.scoring import Score


class DemoPhase(str, Enum):
    WELCOME = "WELCOME"
    CONSENT = "CONSENT"
    NOMINAL_OPERATION = "NOMINAL_OPERATION"
    FAULT_INTRODUCED = "FAULT_INTRODUCED"
    HUMAN_DIAGNOSIS = "HUMAN_DIAGNOSIS"
    HUMAN_ACTION_SELECTION = "HUMAN_ACTION_SELECTION"
    ADMIT_EVALUATION = "ADMIT_EVALUATION"
    EXECUTE = "EXECUTE"
    HOLD = "HOLD"
    OUTCOME = "OUTCOME"
    SESSION_SUMMARY = "SESSION_SUMMARY"


@dataclass(frozen=True)
class ScenarioConfig:
    id: str
    name: str
    source_case_id: str
    fault_time_s: float
    decision_variable: str
    decision_at_or_above: float
    initial_state: dict[str, float]
    fault_overrides: dict[str, float]
    participant_actions: tuple[str, ...] = ()
    diagnosis_options: tuple[str, ...] = ()
    mode: str = "normal"
    seed: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ScenarioConfig":
        data = dict(raw.get("scenario") or raw)
        trigger = dict(data.get("decision_trigger") or {})
        fault = dict(data.get("fault") or {})
        required = ("id", "name", "source_case_id", "initial_state")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"scenario is missing required fields: {missing}")
        if "time_s" not in fault or not fault.get("overrides"):
            raise ValueError("scenario fault requires time_s and non-empty overrides")
        if "variable" not in trigger or "at_or_above" not in trigger:
            raise ValueError("scenario decision_trigger requires variable and at_or_above")
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            source_case_id=str(data["source_case_id"]),
            fault_time_s=float(fault["time_s"]),
            decision_variable=str(trigger["variable"]),
            decision_at_or_above=float(trigger["at_or_above"]),
            initial_state={str(k): float(v) for k, v in dict(data["initial_state"]).items()},
            fault_overrides={str(k): float(v) for k, v in dict(fault["overrides"]).items()},
            participant_actions=tuple(str(v) for v in (data.get("participant_actions") or ())),
            diagnosis_options=tuple(str(v) for v in (data.get("diagnosis_options") or ())),
            mode=str(data.get("mode", "normal")),
            seed=int(data.get("seed", 0)),
        )


@dataclass(frozen=True)
class HumanActionSelection:
    """Controlled participant input; design-time action facts are not accepted."""

    action: str
    params: dict[str, Any] = field(default_factory=dict)
    cited_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    diagnosis: str = ""
    optional_reasoning: str = ""


@dataclass(frozen=True)
class SessionObservation:
    scenario_id: str
    source_case_id: str
    sim_time_s: float
    mode: str
    state: dict[str, float]
    evidence: list[dict]
    verified_checks: list[str]
    diagnosis: str = ""

    @property
    def evidence_ids(self) -> set[str]:
        return {str(entry["id"]) for entry in self.evidence}


@dataclass
class GovernanceDecision:
    record: ActionRecord
    gate_report: GateReport
    score: Score
    artifacts: dict
    pre_action_state: dict[str, float]
    post_action_state: dict[str, float]
    evaluated_state_version: int
    reaches_plant: bool
    action_executed: bool

    @property
    def display_decision(self) -> str:
        if self.score.verdict == "admissible":
            if self.record.action == "escalate_to_operator":
                return "ESCALATION_INITIATED"
            return "ADMISSIBLE"
        if self.score.verdict == "not_evaluable":
            return "UNRESOLVED_HOLD"
        return "BLOCKED"

    @property
    def failed_checks(self) -> list[dict]:
        return [violation.to_dict() for violation in self.gate_report.all_violations()]

    def to_dict(self, include_debug: bool = False) -> dict:
        data = {
            "decision": self.display_decision,
            "canonical_verdict": self.score.verdict,
            "first_failing": self.gate_report.first_failing,
            "aas_code": self.gate_report.aas_code,
            "failed_checks": self.failed_checks,
            "reaches_plant": self.reaches_plant,
            "action_executed": self.action_executed,
            "pre_action_state": dict(self.pre_action_state),
            "post_action_state": dict(self.post_action_state),
        }
        if include_debug:
            data.update(
                action_record=self.record.to_dict(),
                gates=self.gate_report.to_dict(),
                score=self.score.to_dict(),
                artifacts=self.artifacts,
                evaluated_state_version=self.evaluated_state_version,
            )
        return data
