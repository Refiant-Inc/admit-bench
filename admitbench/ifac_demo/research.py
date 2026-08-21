"""Consent-gated, minimal research records and administrative exports."""

from __future__ import annotations

import csv
import json
import math
import statistics
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


STUDY_VERSION = "ifac-cstr-v1"
FORBIDDEN_PII_KEYS = {
    "participant_name",
    "full_name",
    "email",
    "ip",
    "ip_address",
    "user_agent",
    "browser_fingerprint",
    "location",
    "precise_location",
    "device_id",
    "device_identifier",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _assert_no_pii_keys(value: Any) -> None:
    if isinstance(value, dict):
        forbidden = {str(key).lower() for key in value} & FORBIDDEN_PII_KEYS
        if forbidden:
            raise ValueError(f"research record contains forbidden identifying fields: {sorted(forbidden)}")
        for child in value.values():
            _assert_no_pii_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_no_pii_keys(child)


class ResearchStore:
    """Append-only participant records; no consent/session ID means no write."""

    def __init__(
        self,
        root: str | Path = "data/ifac_demo",
        now: Callable[[], datetime] = _utc_now,
    ):
        self.root = Path(root)
        self.sessions_dir = self.root / "sessions"
        self.exports_dir = self.root / "exports"
        self._now = now
        self._lock = threading.RLock()

    def begin_session(self, consented: bool) -> Optional[str]:
        return str(uuid.uuid4()) if consented else None

    @staticmethod
    def _validated_session_id(session_id: str) -> str:
        parsed = uuid.UUID(session_id)
        if str(parsed) != session_id.lower():
            raise ValueError("session ID must be a canonical UUID")
        return str(parsed)

    def persist(self, session_id: Optional[str], payload: dict) -> Optional[Path]:
        if session_id is None:
            return None
        sid = self._validated_session_id(session_id)
        record = {"session_id": sid, "study_version": STUDY_VERSION, **payload}
        record.setdefault("timestamp", self._now().isoformat())
        _assert_no_pii_keys(record)
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            target = self.sessions_dir / f"{sid}.jsonl"
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
            return target

    def records(self) -> list[dict]:
        if not self.sessions_dir.exists():
            return []
        rows = []
        with self._lock:
            for path in sorted(self.sessions_dir.glob("*.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        rows.append(json.loads(line))
        return rows

    def summary(self) -> dict:
        rows = self.records()
        verdicts = Counter((row.get("admit_decision") or {}).get("decision") for row in rows)
        failed_tiers = Counter(
            (row.get("admit_decision") or {}).get("failed_tier")
            for row in rows
            if (row.get("admit_decision") or {}).get("failed_tier")
        )
        failed_codes = Counter()
        actions = Counter()
        response_times = []
        scenarios: dict[str, dict] = {}
        executed = 0
        projected_safe = 0
        projected_crossing = 0
        projected_margins = []

        for row in rows:
            decision = row.get("admit_decision") or {}
            display = decision.get("decision")
            action = (row.get("human_action") or {}).get("action_type")
            if action:
                actions[action] += 1
            for failure in decision.get("failed_checks") or []:
                code = failure.get("code") if isinstance(failure, dict) else None
                if code:
                    failed_codes[code] += 1
            try:
                response = float(row.get("response_time_seconds"))
                if math.isfinite(response) and response >= 0:
                    response_times.append(response)
            except (TypeError, ValueError):
                pass

            scenario_id = str(row.get("scenario_id") or "unknown")
            bucket = scenarios.setdefault(
                scenario_id,
                {"total": 0, "admissible": 0, "blocked": 0, "escalated": 0, "unresolved": 0},
            )
            bucket["total"] += 1
            if display in ("ADMISSIBLE", "ESCALATION_INITIATED"):
                bucket["admissible"] += 1
            if display == "BLOCKED":
                bucket["blocked"] += 1
            elif display == "ESCALATION_INITIATED":
                bucket["escalated"] += 1
            elif display == "UNRESOLVED_HOLD":
                bucket["unresolved"] += 1

            executed += int(bool(row.get("action_executed")))
            # A blocked proposal's stored trajectory is a counterfactual. It
            # remains useful in exports but must not be reported as the live
            # post-governance process outcome.
            if display in ("ADMISSIBLE", "ESCALATION_INITIATED"):
                outcome = row.get("outcome") or {}
                if outcome.get("crossed") is False:
                    projected_safe += 1
                elif outcome.get("crossed") is True:
                    projected_crossing += 1
                try:
                    margin = float(outcome.get("min_margin"))
                    if math.isfinite(margin):
                        projected_margins.append(margin)
                except (TypeError, ValueError):
                    pass

        for bucket in scenarios.values():
            bucket["admissibility_rate"] = round(bucket["admissible"] / bucket["total"], 4)

        histogram_specs = ((0, 5), (5, 10), (10, 20), (20, 30), (30, 60), (60, None))
        response_histogram = []
        for lower, upper in histogram_specs:
            count = sum(
                1 for value in response_times
                if value >= lower and (upper is None or value < upper)
            )
            response_histogram.append(
                {"label": f"{lower}+" if upper is None else f"{lower}–{upper}", "count": count}
            )

        admissible = verdicts.get("ADMISSIBLE", 0) + verdicts.get("ESCALATION_INITIATED", 0)
        return {
            "consented_sessions": len({row.get("session_id") for row in rows}),
            "total_interventions": len(rows),
            "admissible_interventions": admissible,
            "blocked_interventions": verdicts.get("BLOCKED", 0),
            "escalated_interventions": verdicts.get("ESCALATION_INITIATED", 0),
            "unresolved_interventions": verdicts.get("UNRESOLVED_HOLD", 0),
            "admissibility_rate": round(admissible / len(rows), 4) if rows else None,
            "most_common_failed_tier": (
                failed_tiers.most_common(1)[0][0] if failed_tiers else None
            ),
            "most_common_failed_check": (
                failed_codes.most_common(1)[0][0] if failed_codes else None
            ),
            "median_response_time_seconds": (
                round(statistics.median(response_times), 3) if response_times else None
            ),
            "decision_counts": dict(verdicts),
            "failed_tier_counts": dict(failed_tiers),
            "failed_check_counts": dict(failed_codes),
            "action_counts": dict(actions),
            "scenario_metrics": scenarios,
            "response_time_histogram": response_histogram,
            "outcome_metrics": {
                "actions_executed": executed,
                "projected_safe": projected_safe,
                "projected_crossing": projected_crossing,
                "mean_projected_min_margin": (
                    round(statistics.fmean(projected_margins), 4) if projected_margins else None
                ),
            },
        }

    def export(self, format: str) -> Path:
        rows = self.records()
        kind = format.lower()
        if kind not in ("jsonl", "csv"):
            raise ValueError("research export format must be jsonl or csv")
        stamp = self._now().strftime("%Y%m%dT%H%M%SZ")
        suffix = uuid.uuid4().hex[:8]
        with self._lock:
            self.exports_dir.mkdir(parents=True, exist_ok=True)
            target = self.exports_dir / f"ifac_demo_{stamp}_{suffix}.{kind}"
            if kind == "jsonl":
                target.write_text(
                    "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                    encoding="utf-8",
                )
            else:
                self._write_csv(target, rows)
        return target

    @staticmethod
    def _write_csv(path: Path, rows: Iterable[dict]) -> None:
        fields = (
            "session_id",
            "study_version",
            "timestamp",
            "scenario_id",
            "fault_type",
            "fault_time",
            "decision_index",
            "response_time_seconds",
            "action_type",
            "action_params",
            "decision",
            "canonical_verdict",
            "failed_tier",
            "aas_code",
            "failed_checks",
            "action_executed",
            "pre_action_state",
            "post_action_state",
            "outcome",
        )
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                action = row.get("human_action") or {}
                decision = row.get("admit_decision") or {}
                writer.writerow(
                    {
                        "session_id": row.get("session_id"),
                        "study_version": row.get("study_version"),
                        "timestamp": row.get("timestamp"),
                        "scenario_id": row.get("scenario_id"),
                        "fault_type": row.get("fault_type"),
                        "fault_time": row.get("fault_time"),
                        "decision_index": row.get("decision_index"),
                        "response_time_seconds": row.get("response_time_seconds"),
                        "action_type": action.get("action_type"),
                        "action_params": json.dumps(action.get("params") or {}, sort_keys=True),
                        "decision": decision.get("decision"),
                        "canonical_verdict": decision.get("canonical_verdict"),
                        "failed_tier": decision.get("failed_tier"),
                        "aas_code": decision.get("aas_code"),
                        "failed_checks": json.dumps(decision.get("failed_checks") or []),
                        "action_executed": row.get("action_executed"),
                        "pre_action_state": json.dumps(row.get("pre_action_state") or {}, sort_keys=True),
                        "post_action_state": json.dumps(row.get("post_action_state") or {}, sort_keys=True),
                        "outcome": json.dumps(row.get("outcome") or {}, sort_keys=True),
                    }
                )
