"""The rulebook: what each action type requires, fixed at design time.

Written once, read twice — rendered as the agent's instructions and enforced as
the checker's predicate, so instruction and standard cannot drift.

Reversibility is a fact about the action type, not an opinion about the
instance: "a sent shipment cannot be unshipped" is invariant. Instance-level
risk is absorbed where it belongs, in the confidence floor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from admitbench.record import ACTION_CLASSES, BOLDNESS, REVERSIBILITY


@dataclass
class Rule:
    """One entry per action type in the grammar."""

    action: str
    action_class: str  # one rung of the state-effect ladder
    reversibility: str  # undoable | costly_to_undo | permanent
    required_authority: list[str] = field(default_factory=list)
    required_steps: list[str] = field(default_factory=list)  # ordered; must precede the action
    step_evidence_tags: dict[str, list[str]] = field(default_factory=dict)
    required_evidence_tags: list[str] = field(default_factory=list)  # trusted+fresh cover needed to commit
    required_params: list[str] = field(default_factory=list)  # explicit intent the plant needs; never defaulted
    retry_safe: bool = True
    confidence_floor: float = 0.0
    forbidden: bool = False
    allowed_modes: list[str] = field(default_factory=list)  # empty = allowed in all modes
    protective: bool = False  # safe-state actions (controlled shutdown) — conservative by design
    description: str = ""

    def __post_init__(self):
        if self.action_class not in ACTION_CLASSES:
            raise ValueError(f"{self.action}: unknown action class {self.action_class!r}")
        if self.reversibility not in REVERSIBILITY:
            raise ValueError(f"{self.action}: unknown reversibility {self.reversibility!r}")

    @property
    def boldness(self) -> int:
        return BOLDNESS[self.action_class]

    def commits_state(self) -> bool:
        return self.action_class.startswith("commit")

    def to_dict(self) -> dict:
        return asdict(self)


class RuleBook:
    """Action name → Rule. One rulebook per cartridge."""

    def __init__(self, rules: dict[str, Rule]):
        self._rules = dict(rules)

    @classmethod
    def from_entries(cls, entries: list[dict]) -> "RuleBook":
        rules = {}
        for raw in entries:
            data = dict(raw)
            data.setdefault("action_class", data.pop("class", None))
            rule = Rule(
                action=data["action"],
                action_class=data["action_class"],
                reversibility=data.get("reversibility", "undoable"),
                required_authority=list(data.get("required_authority") or []),
                required_steps=list(data.get("required_steps") or []),
                step_evidence_tags={
                    str(step): [str(t) for t in (tags or [])]
                    for step, tags in (data.get("step_evidence_tags") or {}).items()
                },
                required_evidence_tags=list(data.get("required_evidence_tags") or []),
                required_params=list(data.get("required_params") or []),
                retry_safe=bool(data.get("retry_safe", True)),
                confidence_floor=float(data.get("confidence_floor", 0.0)),
                forbidden=bool(data.get("forbidden", False)),
                allowed_modes=list(data.get("allowed_modes") or []),
                protective=bool(data.get("protective", False)),
                description=str(data.get("description", "")),
            )
            if rule.action in rules:
                raise ValueError(f"duplicate rulebook entry: {rule.action}")
            rules[rule.action] = rule
        return cls(rules)

    def get(self, action: str) -> Rule | None:
        return self._rules.get(action)

    def __contains__(self, action: str) -> bool:
        return action in self._rules

    def actions(self) -> list[str]:
        return sorted(self._rules)

    def rules(self) -> list[Rule]:
        return [self._rules[a] for a in self.actions()]

    def attach_procedure(self, action: str, steps: list[str]) -> None:
        """An SOP's steps become the rule's required steps — single source of truth."""
        rule = self._rules.get(action)
        if rule is None:
            raise KeyError(f"procedure references unknown action: {action}")
        rule.required_steps = list(steps)
