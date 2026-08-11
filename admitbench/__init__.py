"""ADMIT Bench — admissibility-first evaluation for industrial AI agents.

The standard in one line: judge the record, not the answer.

The atom is a trace-backed proposed state transition. Safety gates decide whether an
intervention is admissible before any performance score is computed. Failed hard gates
produce no aggregate — unsafe behavior is ineligible for ranking, not merely ranked low.

Built inside-out:

    record      what the agent must put on the record (evidence, action record)
    rulebook    what each action type requires, fixed at design time
    checker     one function, three desks: rehearsal, gate, audit
    gates       T0–T6: compile, evidence, hazard, admissibility, consequence,
                frontier, audit
    physics     the consequence verifier — first-principles worlds (CSTR, column)
    cartridge   the four-file open safety knowledge format
    ablation    evidence degradation for metamorphic tests
    monotonicity the Caution Monotonicity Test: less evidence never justifies
                bolder action
    providers   model access points (openrouter, refiant, anthropic, stub)
    runner      the episode loop
    doctor      the troubleshooter
"""

__version__ = "0.1.0"

from admitbench.record import (  # noqa: F401
    ACTION_CLASSES,
    BOLDNESS,
    REVERSIBILITY,
    ActionRecord,
    DecisionContext,
    Evidence,
    EvidenceLog,
)
from admitbench.rulebook import Rule, RuleBook  # noqa: F401
from admitbench.checker import Violation, admissibility_check  # noqa: F401
