"""Base layer: invariants every episode must satisfy, on every cartridge.

If anything in this file fails, the benchmark's own contract is broken and no
result it produces should be trusted — this is the bench testing itself the
way it tests agents.
"""

import pytest

from admitbench.gates import HARD_GATES, run_gates
from admitbench.providers import get_provider
from admitbench.runner import run_episode
from admitbench.scoring import VERDICT_ADMISSIBLE


def all_cases(cstr, column):
    for cartridge in (cstr, column):
        for case in cartridge.cases:
            yield cartridge, case


def test_the_professional_response_is_admissible_everywhere(cstr, column, oracle):
    """Every case must be winnable by the book — otherwise the case is unfair."""
    for cartridge, case in all_cases(cstr, column):
        result = run_episode(cartridge, case, oracle)
        assert result.score.verdict == VERDICT_ADMISSIBLE, (
            f"{cartridge.id}/{case.id}: oracle got {result.score.aas_code}"
        )
        assert result.score.aggregate is not None


def test_hard_failure_always_means_no_aggregate(cstr, column):
    """The one contract that must never bend, checked across every behavior × case."""
    for behavior in ("oracle", "timid", "reckless", "silent"):
        provider = get_provider("stub", behavior)
        for cartridge, case in all_cases(cstr, column):
            score = run_episode(cartridge, case, provider).score
            if score.verdict == VERDICT_ADMISSIBLE:
                assert score.aggregate is not None
            else:
                assert score.aggregate is None
                assert score.first_failing in HARD_GATES
                assert score.aas_code


def test_no_record_is_never_evaluable(cstr, column, silent):
    for cartridge, case in all_cases(cstr, column):
        score = run_episode(cartridge, case, silent).score
        assert score.verdict == "not_evaluable" and score.aggregate is None


def test_every_trace_is_recorded_and_ledger_attested(cstr, column, oracle):
    for cartridge, case in all_cases(cstr, column):
        result = run_episode(cartridge, case, oracle)
        t6 = result.gate_report.gate("T6")
        assert t6 is not None and t6.info["recorded"]
        assert t6.info["ledger_verified"]
        assert result.trace["ledger_head"]
        assert result.trace["prompts"]["system"] and result.trace["raw_model_text"]


def test_verdicts_replay_deterministically(cstr, oracle):
    """The same inputs must produce the same verdict — audit is a re-run, not a memory."""
    case = cstr.case("C07")
    first = run_episode(cstr, case, oracle)
    log, ctx = cstr.evidence_log_for(case), cstr.context_for(case)
    report, _ = run_gates(
        first.record, case, cstr.rulebook, log, ctx, cstr.world(), horizon_s=cstr.horizon_s
    )
    assert report.to_dict() == first.gate_report.to_dict()


def test_litmus_pair_never_inverts(cstr, oracle):
    """A: correct answer, skipped step → fail. B: good-reason escalation → pass."""
    from admitbench.checker import admissibility_check
    from tests.conftest import make_record

    case = cstr.case("C01")
    log, ctx = cstr.evidence_log_for(case), cstr.context_for(case)
    violations = admissibility_check(
        make_record(checks_performed=[], cited_evidence=["ev_tt101", "ev_ft201"]),
        log, cstr.rulebook, ctx,
    )
    assert any(v.code == "AAS-T3-STEP-MISSING" for v in violations)  # A fails

    result = run_episode(cstr, cstr.case("C02"), oracle)  # corrupted evidence → escalation
    assert result.record.action == "escalate_to_operator"
    assert result.score.verdict == VERDICT_ADMISSIBLE  # B passes


def test_first_failing_gate_is_the_earliest_in_chain_order(cstr, reckless, silent, timid):
    order = {tier: i for i, tier in enumerate(HARD_GATES)}
    for provider in (reckless, silent, timid):
        for case in cstr.cases:
            result = run_episode(cstr, case, provider)
            report = result.gate_report
            failed = [r.tier for r in report.results if not r.passed and r.tier in HARD_GATES]
            if failed:
                assert report.first_failing == min(failed, key=lambda t: order[t])
