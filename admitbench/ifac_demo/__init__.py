"""Backend integration for the IFAC live CSTR demonstrator.

The demo is deliberately an adapter over the existing ADMIT kernel.  Human
input becomes the same :class:`ActionRecord` used by model providers and every
decision is evaluated by :func:`admitbench.gates.run_gates` before the live
session may be changed.
"""

from admitbench.ifac_demo.gateway import ExecutionGateway
from admitbench.ifac_demo.controller import DemoController
from admitbench.ifac_demo.models import (
    DemoPhase,
    GovernanceDecision,
    HumanActionSelection,
    ScenarioConfig,
    SessionObservation,
)
from admitbench.ifac_demo.proposers import ActionProposer, HumanActionProposer
from admitbench.ifac_demo.research import ResearchStore
from admitbench.ifac_demo.session import CSTRDemoSession, load_scenario

__all__ = [
    "ActionProposer",
    "CSTRDemoSession",
    "DemoController",
    "DemoPhase",
    "ExecutionGateway",
    "GovernanceDecision",
    "HumanActionProposer",
    "HumanActionSelection",
    "ResearchStore",
    "ScenarioConfig",
    "SessionObservation",
    "load_scenario",
]
