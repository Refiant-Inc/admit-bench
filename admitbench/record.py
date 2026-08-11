"""The atom: a trace-backed proposed state transition.

Two objects live here.

Evidence is written by the platform, never by the agent — trust attaches to the
channel information arrived from, never to what the content claims about itself.

The action record is what the agent must emit for every action. The output — the
thing benchmarks grade — is one field among many.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from admitbench.canonical import encode as canonical_encode

REVERSIBILITY = ("undoable", "costly_to_undo", "permanent")

# The state-effect ladder. Ordered by how much unsupervised change the action
# commits to the world. "protect" (a safe-state action such as controlled
# shutdown) sits below the commits on unsafe autonomy even though it is a large
# physical intervention: it reduces hazard exposure and hands the plant to a
# well-understood state.
ACTION_CLASSES = (
    "observe",
    "rehearse",
    "hold",
    "escalate",
    "protect",
    "commit_reversible",
    "commit_costly",
    "commit_permanent",
)
BOLDNESS = {name: rank for rank, name in enumerate(ACTION_CLASSES)}

TRUST_LEVELS = ("trusted", "untrusted")
QUALITY_LEVELS = ("good", "stale", "quarantined", "conflicting")


@dataclass
class Evidence:
    """One entry in the evidence log. Platform-authored."""

    id: str
    content: str
    source: str
    trust: str
    received_at: float  # seconds on the episode clock
    tag: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    quality: str = "good"
    attestation: str = ""  # hash chained from the previous entry; ledger-authored

    def payload(self) -> dict:
        data = asdict(self)
        data.pop("attestation")
        return data

    def to_dict(self) -> dict:
        return asdict(self)


GENESIS = "admitbench-ledger-genesis"


def _chain_digest(previous: str, payload: dict) -> str:
    # Canonical serialisation, NOT json.dumps — see ADMIT-WASM SPEC-DELTAS D-13.
    canonical = canonical_encode(payload)
    return hashlib.sha256(f"{previous}|{canonical}".encode()).hexdigest()


class EvidenceLog:
    """The platform's ledger of what arrived, from where, and when.

    The agent cannot author entries here: `admit` derives trust from the
    channel via the cartridge's trust map, and stamps arrival time. A message
    claiming to be a sensor reading is still just a message.

    Entries are hash-chained in admission order: each carries an attestation
    digest over its own content and the previous digest, and the ledger
    exposes the chain head. Rewriting any entry after the fact breaks the
    chain — `verify_chain` is how the gate and the audit desk notice.
    (Platform-side degradation for metamorphic tests re-chains deliberately:
    an ablated ledger is a new attested ledger, not a tampered one.)
    """

    def __init__(
        self,
        trust_map: Optional[dict[str, str]] = None,
        freshness_s: Optional[dict[str, float]] = None,
    ):
        self.trust_map = dict(trust_map or {})
        self.freshness_s = dict(freshness_s or {})
        self._entries: dict[str, Evidence] = {}
        self._order: list[str] = []  # admission order, the chain order
        self._head: str = GENESIS

    # -- platform-side API ---------------------------------------------------

    def admit(
        self,
        id: str,
        content: str,
        source: str,
        received_at: float,
        tag: Optional[str] = None,
        value: Optional[float] = None,
        unit: Optional[str] = None,
        quality: str = "good",
    ) -> Evidence:
        if id in self._entries:
            raise ValueError(f"evidence id already admitted: {id}")
        if quality not in QUALITY_LEVELS:
            raise ValueError(f"unknown evidence quality: {quality}")
        entry = Evidence(
            id=id,
            content=content,
            source=source,
            trust=self.trust_map.get(source, "untrusted"),
            received_at=received_at,
            tag=tag,
            value=value,
            unit=unit,
            quality=quality,
        )
        entry.attestation = _chain_digest(self._head, entry.payload())
        self._head = entry.attestation
        self._entries[id] = entry
        self._order.append(id)
        return entry

    # -- attestation -----------------------------------------------------------

    @property
    def ledger_head(self) -> str:
        return self._head

    def verify_chain(self) -> bool:
        """Recompute the chain from genesis; False means the ledger was rewritten."""
        head = GENESIS
        for eid in self._order:
            entry = self._entries.get(eid)
            if entry is None:
                return False
            head = _chain_digest(head, entry.payload())
            if entry.attestation != head:
                return False
        return head == self._head

    def _rechain(self) -> None:
        """Re-attest the whole ledger after a deliberate platform-side change
        (ablation). Produces a new valid chain — degraded, not tampered."""
        self._order = [eid for eid in self._order if eid in self._entries]
        head = GENESIS
        for eid in self._order:
            entry = self._entries[eid]
            entry.attestation = _chain_digest(head, entry.payload())
            head = entry.attestation
        self._head = head

    # -- read API --------------------------------------------------------------

    def get(self, eid: str) -> Optional[Evidence]:
        return self._entries.get(eid)

    def entries(self) -> list[Evidence]:
        return sorted(self._entries.values(), key=lambda e: e.received_at)

    def ids(self) -> set[str]:
        return set(self._entries)

    def visible_at(self, t: float) -> list[Evidence]:
        """Only what you had at the time."""
        return [e for e in self.entries() if e.received_at <= t]

    def max_age_s(self, entry: Evidence) -> float:
        return float(self.freshness_s.get(entry.source, self.freshness_s.get("default", 600.0)))

    def is_fresh(self, entry: Evidence, at_time: float) -> bool:
        if entry.quality == "stale":
            return False
        return (at_time - entry.received_at) <= self.max_age_s(entry)

    def clone(self) -> "EvidenceLog":
        new = EvidenceLog(self.trust_map, self.freshness_s)
        new._entries = {k: copy.deepcopy(v) for k, v in self._entries.items()}
        new._order = list(self._order)
        new._head = self._head
        return new

    # mutation hooks used only by ablation (the platform degrading its own
    # ledger for metamorphic tests) — not part of the agent-facing surface.
    # Both re-chain: an ablated ledger is a new attested ledger.
    def _replace(self, entry: Evidence) -> None:
        self._entries[entry.id] = entry
        self._rechain()

    def _remove(self, eid: str) -> None:
        self._entries.pop(eid, None)
        self._rechain()

    def to_dicts(self) -> list[dict]:
        return [e.to_dict() for e in self.entries()]


@dataclass
class ActionRecord:
    """What the agent must emit for every action. `output` is one field among many."""

    action: str
    output: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    cited_evidence: list[str] = field(default_factory=list)
    checks_performed: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reversibility: str = "undoable"
    recovery_plan: str = ""
    retry_key: Optional[str] = None
    rationale: str = ""  # audit-only: recorded, never scored
    raw: str = ""  # the original model text, kept for replay

    @classmethod
    def from_dict(cls, data: dict[str, Any], raw: str = "") -> "ActionRecord":
        return cls(
            action=str(data.get("action", "")),
            output=dict(data.get("output") or {}),
            params=dict(data.get("params") or {}),
            cited_evidence=[str(x) for x in (data.get("cited_evidence") or [])],
            checks_performed=[str(x) for x in (data.get("checks_performed") or [])],
            confidence=float(data.get("confidence", 0.0)),
            reversibility=str(data.get("reversibility", "")),
            recovery_plan=str(data.get("recovery_plan", "") or ""),
            retry_key=(str(data["retry_key"]) if data.get("retry_key") else None),
            rationale=str(data.get("rationale", "") or ""),
            raw=raw,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecisionContext:
    """Everything about the moment of decision that is not evidence.

    Authority is granted at engagement start and only narrows. `decided_at` is
    the episode-clock time the record was emitted: a decision may be justified
    only by information that verifiably arrived before it.
    """

    authority_scope: list[str] = field(default_factory=list)
    mode: str = "normal"
    decided_at: float = 0.0
    response_time_s: float = 300.0  # how long a human takes to pick up an escalation

    def to_dict(self) -> dict:
        return asdict(self)
