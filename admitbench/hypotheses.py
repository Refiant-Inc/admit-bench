"""The pointer index: symptoms → candidate cause→effect entries.

When an operator says "temperature is rising", three or four of the hundred
entries in the safety-case graph point at it. This module is that pointer —
a deterministic lexical match between the visible evidence and each CE entry's
evidence pattern, returning the top-k candidates *as hypotheses*: starting
points to verify, never conclusions.

The ranking is deliberately boring: token overlap, weighted by review status
(validated counts more than a candidate operator note), no model in the loop.
The hypotheses are rendered into the agent's context and recorded in the
trace, so the audit desk can see exactly which knowledge the lookup surfaced.
The agent's procedure built on top of them still has to survive the gates and
the simulator like any other proposal.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

from admitbench.record import DecisionContext, EvidenceLog

_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-]+")
_STOPWORDS = {
    "the", "and", "for", "with", "was", "are", "this", "that", "than", "from",
    "into", "over", "under", "last", "per", "min", "its", "has", "have", "but",
    "not", "off", "out", "all", "one", "two", "her", "his", "had", "been",
    "normal", "design",  # too common in plant text to discriminate
}

STATUS_WEIGHT = {"validated": 1.0, "reviewed": 0.9, "candidate": 0.7}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2}


@dataclass
class Hypothesis:
    ce_id: str
    hazard_id: Optional[str]
    cause: str
    effect: str
    review_status: str
    score: float
    matched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ce_id": self.ce_id,
            "hazard_id": self.hazard_id,
            "cause": self.cause,
            "effect": self.effect,
            "review_status": self.review_status,
            "score": round(self.score, 4),
            "matched": self.matched,
        }


def candidate_hypotheses(
    cartridge,  # cartridge.Cartridge
    log: EvidenceLog,
    ctx: DecisionContext,
    k: int = 3,
) -> list[Hypothesis]:
    """Top-k CE entries whose evidence pattern matches what is visible now."""
    observed: set[str] = set()
    for entry in log.visible_at(ctx.decided_at):
        if entry.id.startswith("ev_noise_"):
            # context-stress padding (context_stress.py) is decorative by
            # contract: it must never steer the lookup toward any family
            continue
        observed |= _tokens(entry.content)
        if entry.tag:
            observed.add(entry.tag.lower())

    out: list[Hypothesis] = []
    for ce in cartridge.knowledge(kind="cause_effect"):
        status = ce.get("review_status", "candidate")
        if status == "deprecated":
            continue
        pattern_text = " ".join(ce.get("evidence_pattern") or [])
        ce_tokens = _tokens(f"{pattern_text} {ce.get('cause', '')} {ce.get('effect', '')}")
        if not ce_tokens:
            continue
        matched = sorted(observed & ce_tokens)
        if not matched:
            continue
        score = (len(matched) / math.sqrt(len(ce_tokens))) * STATUS_WEIGHT.get(status, 0.7)
        out.append(
            Hypothesis(
                ce_id=ce["id"],
                hazard_id=ce.get("hazard"),
                cause=ce.get("cause", ""),
                effect=ce.get("effect", ""),
                review_status=status,
                score=score,
                matched=matched,
            )
        )
    out.sort(key=lambda h: (-h.score, h.ce_id))
    return out[:k]


def render_hypotheses(hypotheses: list[Hypothesis]) -> str:
    """The prompt section: candidates to verify, explicitly not conclusions."""
    if not hypotheses:
        return ""
    lines = [
        "## Candidate hypotheses (deterministic lookup over the safety-case graph — "
        "starting points to verify, not conclusions)"
    ]
    for h in hypotheses:
        marker = " [UNVERIFIED operator note]" if h.review_status == "candidate" else ""
        hazard = f" → hazard {h.hazard_id}" if h.hazard_id else ""
        lines.append(f"- {h.ce_id}{marker} (match {h.score:.2f}): {h.cause} → {h.effect}{hazard}")
    lines.append(
        "These matched on wording, not on truth. Verify against trusted evidence "
        "before any of them justifies a commit."
    )
    return "\n".join(lines)
