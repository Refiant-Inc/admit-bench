"""Ablation: the platform degrading its own evidence ledger, deliberately.

Metamorphic testing needs controlled degradation — remove an entry, freeze it,
quarantine its instrument, stop trusting its channel — while everything else
stays fixed. The comparison between the full run and the degraded run is the
test; no answer key is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from admitbench.record import EvidenceLog

OPS = ("remove", "stale", "quarantine", "untrust")


@dataclass
class AblationSpec:
    id: str
    op: str
    evidence_ids: list[str] = field(default_factory=list)
    source: Optional[str] = None  # for op="untrust"
    purpose: str = ""
    decorative: bool = False  # the removed evidence was decoration; action may hold
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "AblationSpec":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "op": self.op,
            "evidence_ids": self.evidence_ids,
            "source": self.source,
            "purpose": self.purpose,
            "decorative": self.decorative,
        }


def apply_ablation(log: EvidenceLog, spec: AblationSpec) -> EvidenceLog:
    if spec.op not in OPS:
        raise ValueError(f"unknown ablation op {spec.op!r}; known: {OPS}")

    degraded = log.clone()
    if spec.op == "remove":
        for eid in spec.evidence_ids:
            degraded._remove(eid)
    elif spec.op in ("stale", "quarantine"):
        quality = "stale" if spec.op == "stale" else "quarantined"
        for eid in spec.evidence_ids:
            entry = degraded.get(eid)
            if entry is None:
                raise KeyError(f"ablation {spec.id}: no evidence {eid!r} to degrade")
            entry.quality = quality
            degraded._replace(entry)
    elif spec.op == "untrust":
        if not spec.source:
            raise ValueError(f"ablation {spec.id}: op=untrust requires a source")
        degraded.trust_map[spec.source] = "untrusted"
        for entry in degraded.entries():
            if entry.source == spec.source:
                entry.trust = "untrusted"
                degraded._replace(entry)
    return degraded
