"""The cartridge: four files, four questions.

    manifest.yaml            what system are we evaluating, who is the agent,
                             what actions exist, what sources are trusted
    system_graph.jsonl       what exists physically — assets, tags, limits
    safety_case_graph.jsonl  what can go wrong and why — hazards, cause→effect,
                             unsafe actions, recovery plans, evidence patterns
    procedures_cases.jsonl   what to do and how we test it — SOPs and cases

This is the open safety knowledge format: anything from a one-paragraph prompt
to a stack of P&ID/HAZOP documents compiles down to these four files. Knowledge
entries carry a review_status (candidate → reviewed → validated → deprecated)
so operator tacit knowledge can enter as candidate cause→effect entries without
instantly becoming a hard rule.

Loading a cartridge IS gate T0's compile stage: a cartridge that does not
validate cannot produce evaluable episodes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from admitbench.physics import WORLDS, World, world_for
from admitbench.record import DecisionContext, EvidenceLog
from admitbench.rulebook import RuleBook

REVIEW_STATUSES = ("candidate", "reviewed", "validated", "deprecated")

FILES = {
    "manifest": "manifest.yaml",
    "system": "system_graph.jsonl",
    "safety_case": "safety_case_graph.jsonl",
    "procedures": "procedures_cases.jsonl",
}


class CartridgeError(Exception):
    def __init__(self, path, problems: list[str]):
        self.path = str(path)
        self.problems = problems
        super().__init__(
            f"cartridge at {path} failed to compile "
            f"({len(problems)} problem{'s' if len(problems) != 1 else ''}):\n  - "
            + "\n  - ".join(problems)
        )


@dataclass
class Case:
    """One benchmark episode: a situation, an evidence stream, and what a
    professional response looks like."""

    id: str
    title: str
    mode: str = "normal"
    hazard_id: Optional[str] = None
    diagnosis_accept: list[str] = field(default_factory=list)
    initial_state: dict = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    load_bearing: list[str] = field(default_factory=list)
    oracle: Optional[dict] = None  # {"action": ..., "params": {...}}
    acceptable_actions: list[str] = field(default_factory=list)
    escalation_ok: bool = False
    ablations: list[dict] = field(default_factory=list)
    decision_time_s: Optional[float] = None
    time_to_hazard_s: Optional[float] = None
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Case":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def decided_at(self) -> float:
        if self.decision_time_s is not None:
            return float(self.decision_time_s)
        arrivals = [float(e.get("received_at", 0.0)) for e in self.evidence]
        return (max(arrivals) if arrivals else 0.0) + 5.0


@dataclass
class Cartridge:
    path: Path
    manifest: dict
    system: list[dict]
    safety_case: list[dict]
    procedures: list[dict]
    cases: list[Case]
    rulebook: RuleBook
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ properties

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @property
    def title(self) -> str:
        return self.manifest.get("title", self.manifest["id"])

    @property
    def world_name(self) -> str:
        return self.manifest["world"]

    def world(self) -> World:
        return world_for(self.world_name)

    @property
    def weights(self) -> dict:
        return dict((self.manifest.get("scoring") or {}).get("weights") or {})

    @property
    def exposure_weights(self) -> dict:
        return dict((self.manifest.get("scoring") or {}).get("exposure_weights") or {})

    @property
    def horizon_s(self) -> float:
        return float(self.manifest.get("horizon_s", 1200.0))

    @property
    def response_time_s(self) -> float:
        return float(self.manifest.get("response_time_s", 300.0))

    def content_hash(self) -> str:
        """sha256 over the four files — the version that actually ran, not the
        version the manifest claims. Cached per instance."""
        cached = self.__dict__.get("_content_hash")
        if cached:
            return cached
        import hashlib
        digest = hashlib.sha256()
        for name in sorted(FILES.values()):
            file = self.path / name
            if file.exists():
                digest.update(name.encode())
                digest.update(file.read_bytes())
        self.__dict__["_content_hash"] = digest.hexdigest()[:16]
        return self.__dict__["_content_hash"]

    # --------------------------------------------------------------- lookups

    def case(self, case_id: str) -> Case:
        for c in self.cases:
            if c.id == case_id:
                return c
        raise KeyError(f"no case {case_id!r} in cartridge {self.id}; have {[c.id for c in self.cases]}")

    def knowledge(self, kind: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        out = self.safety_case
        if kind:
            out = [e for e in out if e.get("kind") == kind]
        if status:
            out = [e for e in out if e.get("review_status") == status]
        return out

    def recovery_for(self, action: str) -> Optional[str]:
        for entry in self.knowledge(kind="recovery"):
            if entry.get("for_action") == action and entry.get("review_status") != "deprecated":
                return entry.get("plan")
        return None

    def sops(self) -> list[dict]:
        return [p for p in self.procedures if p.get("kind") == "procedure"]

    def tags(self) -> dict[str, dict]:
        return {e["id"]: e for e in self.system if e.get("kind") == "tag"}

    # ------------------------------------------------------------- factories

    def evidence_log_for(self, case: Case) -> EvidenceLog:
        log = EvidenceLog(
            trust_map=self.manifest.get("trust_map") or {},
            freshness_s=self.manifest.get("freshness_s") or {},
        )
        for e in case.evidence:
            log.admit(
                id=e["id"],
                content=e.get("content", ""),
                source=e.get("source", "unknown"),
                received_at=float(e.get("received_at", 0.0)),
                tag=e.get("tag"),
                value=e.get("value"),
                unit=e.get("unit"),
                quality=e.get("quality", "good"),
            )
        return log

    def context_for(self, case: Case, decided_at: Optional[float] = None) -> DecisionContext:
        agent = self.manifest.get("agent") or {}
        return DecisionContext(
            authority_scope=list(agent.get("authority_scope") or []),
            mode=case.mode,
            decided_at=case.decided_at() if decided_at is None else decided_at,
            response_time_s=self.response_time_s,
        )


# ---------------------------------------------------------------------------
# Loading and validation — the T0 compile stage
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    entries = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CartridgeError(path.parent, [f"{path.name}:{n}: invalid JSON — {exc}"]) from None
    return entries


def validate_cartridge(path: str | Path) -> tuple[list[str], list[str]]:
    """Returns (errors, warnings). Errors mean the cartridge cannot compile."""
    from admitbench.paths import resolve_cartridge

    path = resolve_cartridge(path)
    errors: list[str] = []
    warnings: list[str] = []

    for key, fname in FILES.items():
        if not (path / fname).exists():
            errors.append(f"missing file: {fname}")
    if errors:
        return errors, warnings

    try:
        manifest = yaml.safe_load((path / FILES["manifest"]).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [f"manifest.yaml: invalid YAML — {exc}"], warnings

    for req in ("id", "world", "actions", "trust_map", "agent"):
        if req not in manifest:
            errors.append(f"manifest.yaml: missing required key {req!r}")
    if errors:
        return errors, warnings

    if manifest["world"] not in WORLDS:
        errors.append(f"manifest.yaml: unknown world {manifest['world']!r}; available: {sorted(WORLDS)}")
    if not (manifest.get("agent") or {}).get("authority_scope"):
        errors.append("manifest.yaml: agent.authority_scope is empty — authority is granted at engagement start")

    try:
        rulebook = RuleBook.from_entries(manifest["actions"])
    except (ValueError, KeyError) as exc:
        errors.append(f"manifest.yaml: bad action grammar — {exc}")
        return errors, warnings

    weights = (manifest.get("scoring") or {}).get("weights")
    if weights and abs(sum(weights.values()) - 1.0) > 1e-6:
        errors.append(f"manifest.yaml: scoring weights sum to {sum(weights.values())}, expected 1.0")

    try:
        system = _read_jsonl(path / FILES["system"])
        safety_case = _read_jsonl(path / FILES["safety_case"])
        procedures = _read_jsonl(path / FILES["procedures"])
    except CartridgeError as exc:
        return list(exc.problems), warnings

    tag_ids = {e["id"] for e in system if e.get("kind") == "tag"}
    hazard_ids = {e["id"] for e in safety_case if e.get("kind") == "hazard"}

    for entry in safety_case:
        status = entry.get("review_status", "candidate")
        if status not in REVIEW_STATUSES:
            errors.append(f"safety_case_graph: {entry.get('id')}: bad review_status {status!r}")
        elif status == "candidate":
            warnings.append(
                f"safety_case_graph: {entry.get('id')} is a candidate entry "
                "(rendered to the agent as unverified; never a hard rule)"
            )

    for proc in (p for p in procedures if p.get("kind") == "procedure"):
        target = proc.get("applies_to")
        if target and target not in rulebook:
            errors.append(f"procedures: {proc.get('id')}: applies_to unknown action {target!r}")

    seen_cases: set[str] = set()
    for raw in (p for p in procedures if p.get("kind") == "case"):
        cid = raw.get("id", "<missing id>")
        where = f"case {cid}"
        if cid in seen_cases:
            errors.append(f"{where}: duplicate case id")
        seen_cases.add(cid)

        case = Case.from_dict(raw)
        ev_ids = set()
        for e in case.evidence:
            if e["id"] in ev_ids:
                errors.append(f"{where}: duplicate evidence id {e['id']}")
            ev_ids.add(e["id"])
            src = e.get("source")
            if src not in (manifest.get("trust_map") or {}):
                errors.append(f"{where}: evidence {e['id']} has source {src!r} not in trust_map")
            tag = e.get("tag")
            if tag and tag not in tag_ids:
                errors.append(f"{where}: evidence {e['id']} references tag {tag!r} not in system_graph")

        for eid in case.load_bearing:
            if eid not in ev_ids:
                errors.append(f"{where}: load_bearing evidence {eid!r} does not exist")

        if case.hazard_id and case.hazard_id not in hazard_ids:
            errors.append(f"{where}: hazard {case.hazard_id!r} not in safety_case_graph")

        # single-token accept phrases match far too loosely ('coolant' inside
        # 'no coolant issue found'); the floor forces discriminating phrases
        import re as _re
        for phrase in case.diagnosis_accept or []:
            if len(_re.sub(r"[^a-z0-9]+", " ", phrase.lower()).split()) < 2:
                errors.append(
                    f"{where}: diagnosis_accept phrase {phrase!r} has fewer than 2 tokens — "
                    "too loose to discriminate; use a fuller phrase or rely on output.hazard_id"
                )

        for name in ([case.oracle["action"]] if case.oracle else []) + list(case.acceptable_actions):
            if name not in rulebook:
                errors.append(f"{where}: action {name!r} not in the action grammar")

        if case.oracle:
            # coaching lint: evidence that states the oracle action verbatim is
            # an answer key leaking into the prompt-visible material
            phrase = str(case.oracle.get("action", "")).replace("_", " ").strip().lower()
            for e in case.evidence:
                if phrase and phrase in str(e.get("content", "")).lower():
                    warnings.append(
                        f"{where}: evidence {e['id']} states the oracle action verbatim "
                        f"({phrase!r}) — possible coaching; rephrase the evidence"
                    )

            oracle_rule_params = rulebook.get(case.oracle["action"])
            if oracle_rule_params is not None:
                given = case.oracle.get("params") or {}
                for param in oracle_rule_params.required_params:
                    if given.get(param) in (None, ""):
                        errors.append(
                            f"{where}: oracle action {case.oracle['action']!r} omits required "
                            f"params.{param} — the answer key must satisfy the grammar it is judged by"
                        )

            # the oracle itself must be admissible: a commit oracle needs trusted
            # good coverage of its required tags inside the case's own evidence
            oracle_rule = rulebook.get(case.oracle["action"])
            if oracle_rule and oracle_rule.commits_state():
                trust_map = manifest.get("trust_map") or {}
                for tag in oracle_rule.required_evidence_tags:
                    covered = any(
                        e.get("tag") == tag
                        and trust_map.get(e.get("source")) == "trusted"
                        and e.get("quality", "good") == "good"
                        for e in case.evidence
                    )
                    if not covered:
                        errors.append(
                            f"{where}: oracle {case.oracle['action']!r} requires trusted "
                            f"evidence for {tag} but the case provides none — the oracle "
                            "itself would be inadmissible"
                        )

        for ab in case.ablations:
            for eid in ab.get("evidence_ids") or []:
                if eid not in ev_ids:
                    errors.append(f"{where}: ablation {ab.get('id')} references unknown evidence {eid!r}")

    return errors, warnings


def load_cartridge(path: str | Path) -> Cartridge:
    from admitbench.paths import resolve_cartridge

    path = resolve_cartridge(path)
    errors, warnings = validate_cartridge(path)
    if errors:
        raise CartridgeError(path, errors)

    manifest = yaml.safe_load((path / FILES["manifest"]).read_text(encoding="utf-8"))
    system = _read_jsonl(path / FILES["system"])
    safety_case = _read_jsonl(path / FILES["safety_case"])
    procedures = _read_jsonl(path / FILES["procedures"])

    rulebook = RuleBook.from_entries(manifest["actions"])
    # SOPs are written once, read twice: rendered as instructions in the prompt
    # and enforced here as the rule's required steps
    for proc in procedures:
        if proc.get("kind") == "procedure" and proc.get("applies_to"):
            rulebook.attach_procedure(proc["applies_to"], proc.get("steps") or [])

    cases = [Case.from_dict(p) for p in procedures if p.get("kind") == "case"]
    for case in cases:
        if case.hazard_id and not case.diagnosis_accept:
            case.diagnosis_accept = [case.hazard_id]

    return Cartridge(
        path=path,
        manifest=manifest,
        system=system,
        safety_case=safety_case,
        procedures=procedures,
        cases=cases,
        rulebook=rulebook,
        warnings=warnings,
    )
