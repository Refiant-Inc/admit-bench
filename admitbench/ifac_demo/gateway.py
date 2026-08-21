"""The sole evaluate-then-conditionally-execute path for the live CSTR."""

from __future__ import annotations

from admitbench.ifac_demo.governance import evaluate_action
from admitbench.ifac_demo.models import GovernanceDecision, HumanActionSelection
from admitbench.ifac_demo.proposers import HumanActionProposer
from admitbench.ifac_demo.session import CSTRDemoSession


class ExecutionGateway:
    def submit_human_action(
        self,
        session: CSTRDemoSession,
        selection: HumanActionSelection,
    ) -> GovernanceDecision:
        """Atomically bind, evaluate, and optionally apply a participant action."""
        with session.transaction():
            evaluated_version = session.begin_evaluation()
            pre_state = session.state
            try:
                observation = session.observation()
                proposer = HumanActionProposer(
                    selection,
                    session.cartridge.rulebook,
                    session.cartridge.recovery_for,
                )
                record = proposer.propose(observation)
                case, evidence, context = session.decision_inputs()
                report, artifacts, score = evaluate_action(
                    session.cartridge,
                    case,
                    record,
                    evidence,
                    context,
                    session.world,
                )
                reaches_plant = score.verdict == "admissible"
                executed = False
                if reaches_plant:
                    executed = session._apply_authorized(record, report, evaluated_version)
                decision = GovernanceDecision(
                    record=record,
                    gate_report=report,
                    score=score,
                    artifacts=artifacts,
                    pre_action_state=pre_state,
                    post_action_state=session.state,
                    evaluated_state_version=evaluated_version,
                    reaches_plant=reaches_plant,
                    action_executed=executed,
                )
                session.finish_decision(decision)
                return decision
            except Exception:
                session.cancel_evaluation()
                raise
