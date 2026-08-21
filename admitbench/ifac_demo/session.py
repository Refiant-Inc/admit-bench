"""Deterministic mutable CSTR session built around the existing world model."""

from __future__ import annotations

import copy
import math
import threading
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Iterator, Optional

import yaml

from admitbench.cartridge import Cartridge, Case, load_cartridge
from admitbench.gates import GateReport
from admitbench.record import ActionRecord, DecisionContext, EvidenceLog
from admitbench.ifac_demo.models import DemoPhase, ScenarioConfig, SessionObservation


DEFAULT_SCENARIO = Path(__file__).resolve().parent / "scenarios" / "conference_cstr.yaml"


def load_scenario(path: str | Path | None = None) -> ScenarioConfig:
    source = Path(path) if path is not None else DEFAULT_SCENARIO
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return ScenarioConfig.from_dict(data)


class CSTRDemoSession:
    """One participant's live, authoritative plant state.

    State is exposed only as copies.  The sole live-action method is guarded by
    a passing GateReport and a state-version check; the public entry point is
    :class:`ExecutionGateway`.
    """

    _CHECK_TAGS = {
        "verify_reactor_temperature": ("TT-101",),
        "verify_coolant_flow": ("FT-201",),
        "check_valve_lineup": ("FC-201",),
        "confirm_hazard_signal": ("TT-101",),
    }

    def __init__(
        self,
        scenario: ScenarioConfig | None = None,
        cartridge: Cartridge | None = None,
    ):
        self.scenario = scenario or load_scenario()
        self.cartridge = cartridge or load_cartridge("cstr")
        if self.cartridge.world_name != "cstr":
            raise ValueError("IFAC CSTR session requires a cartridge using the cstr world")
        unknown_actions = set(self.scenario.participant_actions) - set(self.cartridge.rulebook.actions())
        if unknown_actions:
            raise ValueError(f"scenario contains unknown participant actions: {sorted(unknown_actions)}")
        self.source_case = self.cartridge.case(self.scenario.source_case_id)
        self.world = self.cartridge.world()
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.RLock()):
            state = self.world.initial_state(self.scenario.initial_state)
            unknown = (set(self.scenario.fault_overrides) | {self.scenario.decision_variable}) - set(state)
            if unknown:
                raise ValueError(f"scenario references unknown CSTR state variables: {sorted(unknown)}")
            self._state = state
            self._sim_time_s = 0.0
            self._state_version = 0
            self._fault_injected = False
            self._fault_injected_at_s: Optional[float] = None
            self._phase = DemoPhase.WELCOME
            self._consent = False
            self._diagnosis = ""
            self._verified_checks: list[str] = []
            self._evidence_log = EvidenceLog(
                trust_map=self.cartridge.manifest.get("trust_map") or {},
                freshness_s=self.cartridge.manifest.get("freshness_s") or {},
            )
            self._visible_evidence_ids: list[str] = []
            self._observed_version: Optional[int] = None
            self._history: list[dict] = []
            self._live_execution_count = 0
            self._last_decision = None
            self._record_history("reset")

    @property
    def phase(self) -> DemoPhase:
        return self._phase

    @property
    def state(self) -> dict[str, float]:
        with self._lock:
            return dict(self._state)

    @property
    def sim_time_s(self) -> float:
        return self._sim_time_s

    @property
    def state_version(self) -> int:
        return self._state_version

    @property
    def fault_injected(self) -> bool:
        return self._fault_injected

    @property
    def fault_injected_at_s(self) -> Optional[float]:
        return self._fault_injected_at_s

    @property
    def history(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._history)

    @property
    def live_execution_count(self) -> int:
        return self._live_execution_count

    @property
    def verified_checks(self) -> list[str]:
        return list(self._verified_checks)

    def show_consent(self) -> None:
        with self._lock:
            if self._phase != DemoPhase.WELCOME:
                raise RuntimeError(f"cannot show consent from {self._phase.value}")
            self._phase = DemoPhase.CONSENT

    def start(self, consent: bool = False) -> None:
        with self._lock:
            if self._phase not in (DemoPhase.WELCOME, DemoPhase.CONSENT):
                raise RuntimeError(f"cannot start from {self._phase.value}")
            self._consent = bool(consent)
            self._phase = DemoPhase.NOMINAL_OPERATION

    def advance(self, seconds: float) -> float:
        """Advance real CSTR dynamics, stopping at the participant decision."""
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("advance seconds must be a finite non-negative number")
        with self._lock:
            running = self._phase in (DemoPhase.NOMINAL_OPERATION, DemoPhase.FAULT_INTRODUCED)
            authorized_outcome = (
                self._phase == DemoPhase.OUTCOME
                and self._last_decision is not None
                and self._last_decision.reaches_plant
            )
            if not running and not authorized_outcome:
                raise RuntimeError(f"plant clock is paused in {self._phase.value}")
            target = self._sim_time_s + seconds
            while self._sim_time_s < target - 1e-12:
                if not self._fault_injected and self._sim_time_s >= self.scenario.fault_time_s - 1e-12:
                    self._inject_fault()
                boundary = target
                if not self._fault_injected:
                    boundary = min(boundary, self.scenario.fault_time_s)
                dt = min(self.world.dt_s, boundary - self._sim_time_s)
                if dt <= 1e-12:
                    continue
                self._state = self.world.step(self._state, dt)
                self._sim_time_s += dt
                self._state_version += 1
                self._record_history("step")
                if not self._fault_injected and self._sim_time_s >= self.scenario.fault_time_s - 1e-12:
                    self._inject_fault()
                if self._last_decision is None and self._fault_injected and self._decision_triggered():
                    self._phase = DemoPhase.HUMAN_DIAGNOSIS
                    self._admit_visible_observations()
                    break
            return self._sim_time_s

    def _decision_triggered(self) -> bool:
        return self._state[self.scenario.decision_variable] >= self.scenario.decision_at_or_above

    def _inject_fault(self) -> None:
        self._state.update(self.scenario.fault_overrides)
        self._fault_injected = True
        self._fault_injected_at_s = self._sim_time_s
        self._state_version += 1
        self._phase = DemoPhase.FAULT_INTRODUCED
        self._record_history("fault")

    def _record_history(self, event: str) -> None:
        self._history.append(
            {
                "time_s": round(self._sim_time_s, 6),
                "event": event,
                "phase": self._phase.value,
                **{key: float(value) for key, value in self._state.items()},
            }
        )

    def _admit_visible_observations(self) -> None:
        if self._observed_version == self._state_version:
            return
        # An authorized action can change state without advancing the episode
        # clock, so the state version is part of the evidence identity.
        stamp = f"{int(round(self._sim_time_s * 1000)):09d}_v{self._state_version}"
        measurements = (
            ("TT-101", "historian", self._state["T"], "K", "reactor temperature"),
            ("FT-201", "historian", self._state["coolant_flow"], "%", "coolant flow"),
            ("FC-201", "control_system", self._state["coolant_flow"], "%", "coolant control input"),
            ("CA-101", "control_system", self._state["Ca"], "mol/L", "reactant concentration"),
        )
        latest = []
        for tag, source, value, unit, label in measurements:
            eid = f"ifac_{tag.lower().replace('-', '')}_{stamp}"
            self._evidence_log.admit(
                id=eid,
                content=f"{tag} {label} = {value:.4g} {unit}",
                source=source,
                received_at=self._sim_time_s,
                tag=tag,
                value=float(value),
                unit=unit,
            )
            latest.append(eid)
        self._visible_evidence_ids = latest
        self._observed_version = self._state_version

    def visible_evidence(self) -> list[dict]:
        with self._lock:
            self._admit_visible_observations()
            return [self._evidence_log.get(eid).to_dict() for eid in self._visible_evidence_ids]

    def evidence_ids_for_tags(self, *tags: str) -> list[str]:
        wanted = set(tags)
        return [entry["id"] for entry in self.visible_evidence() if entry.get("tag") in wanted]

    def perform_check(self, check: str) -> None:
        with self._lock:
            if self._phase not in (DemoPhase.HUMAN_DIAGNOSIS, DemoPhase.HUMAN_ACTION_SELECTION):
                raise RuntimeError(f"cannot perform a participant check in {self._phase.value}")
            required_tags = self._CHECK_TAGS.get(check)
            if required_tags is None:
                raise ValueError(f"unsupported live check: {check}")
            visible_tags = {entry.get("tag") for entry in self.visible_evidence()}
            missing = set(required_tags) - visible_tags
            if missing:
                raise RuntimeError(f"check {check} lacks visible signals: {sorted(missing)}")
            if check not in self._verified_checks:
                self._verified_checks.append(check)

    def record_diagnosis(self, diagnosis: str) -> None:
        with self._lock:
            if self._phase != DemoPhase.HUMAN_DIAGNOSIS:
                raise RuntimeError(f"cannot record diagnosis from {self._phase.value}")
            if not diagnosis.strip():
                raise ValueError("diagnosis must not be empty")
            self._diagnosis = diagnosis.strip()
            self._phase = DemoPhase.HUMAN_ACTION_SELECTION

    def observation(self) -> SessionObservation:
        with self._lock:
            if self._phase not in (DemoPhase.HUMAN_ACTION_SELECTION, DemoPhase.ADMIT_EVALUATION):
                raise RuntimeError(f"no action observation is available in {self._phase.value}")
            return SessionObservation(
                scenario_id=self.scenario.id,
                source_case_id=self.source_case.id,
                sim_time_s=self._sim_time_s,
                mode=self.scenario.mode,
                state=dict(self._state),
                evidence=self.visible_evidence(),
                verified_checks=list(self._verified_checks),
                diagnosis=self._diagnosis,
            )

    def runtime_case(self) -> Case:
        """Bind sealed case semantics to the current, real simulator state."""
        with self._lock:
            visible = self.visible_evidence()
            source_by_id = {entry["id"]: entry for entry in self.source_case.evidence}
            load_tags = {
                source_by_id[eid].get("tag")
                for eid in self.source_case.load_bearing
                if eid in source_by_id and source_by_id[eid].get("tag")
            }
            live_load_bearing = [entry["id"] for entry in visible if entry.get("tag") in load_tags]
            return replace(
                self.source_case,
                id=f"{self.source_case.id}@{self._sim_time_s:.3f}",
                mode=self.scenario.mode,
                initial_state=dict(self._state),
                evidence=copy.deepcopy(visible),
                load_bearing=live_load_bearing,
                decision_time_s=self._sim_time_s,
                ablations=[],
            )

    def decision_inputs(self) -> tuple[Case, EvidenceLog, DecisionContext]:
        with self._lock:
            case = self.runtime_case()
            ctx = self.cartridge.context_for(case, decided_at=self._sim_time_s)
            return case, self._evidence_log.clone(), ctx

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            yield

    def begin_evaluation(self) -> int:
        if self._phase != DemoPhase.HUMAN_ACTION_SELECTION:
            raise RuntimeError(f"cannot evaluate from {self._phase.value}")
        self._phase = DemoPhase.ADMIT_EVALUATION
        return self._state_version

    def cancel_evaluation(self) -> None:
        if self._phase == DemoPhase.ADMIT_EVALUATION:
            self._phase = DemoPhase.HUMAN_ACTION_SELECTION

    def _apply_authorized(
        self,
        record: ActionRecord,
        report: GateReport,
        evaluated_state_version: int,
    ) -> bool:
        """Apply to the live state only after a current, passing gate report."""
        if report.hard_failed:
            raise PermissionError("a hard-gate failure cannot reach the live CSTR")
        if evaluated_state_version != self._state_version:
            raise RuntimeError("plant state changed after evaluation; re-evaluation is required")
        rule = self.cartridge.rulebook.get(record.action)
        if rule is None:
            raise PermissionError("an unknown action cannot reach the live CSTR")
        self._state = self.world.apply_action(dict(self._state), record.action, record.params)
        physical = rule.commits_state() or rule.protective
        if physical:
            self._state_version += 1
            self._live_execution_count += 1
            self._record_history("authorized_action")
            self._phase = DemoPhase.EXECUTE
        else:
            self._phase = DemoPhase.HOLD
        return physical

    def finish_decision(self, decision) -> None:
        self._last_decision = decision
        self._phase = DemoPhase.OUTCOME

    def show_summary(self) -> None:
        with self._lock:
            if self._phase != DemoPhase.OUTCOME:
                raise RuntimeError(f"cannot show summary from {self._phase.value}")
            self._phase = DemoPhase.SESSION_SUMMARY

    def new_participant(self) -> None:
        self.reset()
