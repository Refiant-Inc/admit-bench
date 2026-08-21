"""Action-proposer boundary shared by the human demo and a future LLM."""

from __future__ import annotations

import uuid
from typing import Callable, Protocol

from admitbench.record import ActionRecord
from admitbench.rulebook import RuleBook
from admitbench.ifac_demo.models import HumanActionSelection, SessionObservation


class ActionProposer(Protocol):
    """Anything upstream may propose; everything downstream stays identical."""

    def propose(self, observation: SessionObservation) -> ActionRecord:
        ...


class HumanActionProposer:
    """Map controlled participant input onto the canonical ADMIT record.

    Reversibility, retry behaviour, and recovery knowledge are design-time
    facts.  They are derived from the cartridge rather than trusted from the
    browser.  Evidence citations and completed checks must already be present
    in the server-authored observation.
    """

    def __init__(
        self,
        selection: HumanActionSelection,
        rulebook: RuleBook,
        recovery_for: Callable[[str], str | None],
        retry_key_factory: Callable[[], str] | None = None,
    ):
        self.selection = selection
        self.rulebook = rulebook
        self.recovery_for = recovery_for
        self.retry_key_factory = retry_key_factory or (lambda: str(uuid.uuid4()))

    def propose(self, observation: SessionObservation) -> ActionRecord:
        unknown_citations = set(self.selection.cited_evidence) - observation.evidence_ids
        if unknown_citations:
            raise ValueError(
                "human proposal cites evidence that was not visible in this session: "
                f"{sorted(unknown_citations)}"
            )

        rule = self.rulebook.get(self.selection.action)
        # Unknown actions are still represented and reach canonical T0, which
        # is responsible for returning AAS-T0-UNKNOWN-ACTION.
        reversibility = rule.reversibility if rule else "undoable"
        recovery_plan = self.recovery_for(self.selection.action) or ""
        retry_key = self.retry_key_factory() if rule and not rule.retry_safe else None
        diagnosis = self.selection.diagnosis.strip() or observation.diagnosis.strip()

        return ActionRecord(
            action=self.selection.action,
            output={"diagnosis": diagnosis} if diagnosis else {},
            params=dict(self.selection.params),
            cited_evidence=list(self.selection.cited_evidence),
            checks_performed=list(observation.verified_checks),
            confidence=float(self.selection.confidence),
            reversibility=reversibility,
            recovery_plan=recovery_plan,
            retry_key=retry_key,
            rationale=self.selection.optional_reasoning.strip(),
        )
