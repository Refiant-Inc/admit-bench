"""The four evaluation lanes: compliance metrics, repair loop, prompt styles,
context-length stress."""

import json

import pytest

from admitbench.checker import Violation
from admitbench.compliance import compliance_from_traces, compliance_table
from admitbench.context_stress import make_noise_evidence, pad_case, run_context_point
from admitbench.prompts import STYLES, render_system, render_user
from admitbench.providers import Completion, Provider
from admitbench.repair import repair_metrics, run_repair, sanitize_violations
from admitbench.runner import run_episode


# ---- compliance ------------------------------------------------------------

def trace(record=None, latency=2.0, out_tokens=200):
    return {
        "prompts": {"system": "s" * 100, "user": "u" * 100},
        "completion": {"latency_s": latency, "input_tokens": 1000, "output_tokens": out_tokens},
        "action_record": record,
    }


def test_compliance_rates_separate_format_from_judgment(cstr):
    traces = [
        trace(record=None),  # never parsed
        trace(record={"action": "verify_and_adjust", "confidence": 0.8, "reversibility": "undoable"}),
        trace(record={"action": "hold_and_monitor", "confidence": 1.4, "reversibility": "undoable"}),
        trace(record={"action": "increase_coolant_flow", "confidence": 0.9,
                      "reversibility": "undoable", "params": {"delta_pct": 50}}),
        trace(record={"action": "reduce_feed_rate", "confidence": 0.9,
                      "reversibility": "undoable", "params": {"target": "minimum"}}),
    ]
    rates = compliance_from_traces(traces, cstr.rulebook).rates()
    assert rates["episodes"] == 5
    assert rates["schema_valid_rate"] == 0.8      # 4/5 parsed
    assert rates["grammar_valid_rate"] == 0.75    # 3/4 in grammar
    assert rates["fields_valid_rate"] == 0.5      # confidence 1.4 fails fields
    assert rates["params_canonical_rate"] == 0.5  # one of two commits has delta_pct
    assert rates["latency_s"]["mean"] == 2.0
    table = compliance_table({"m": compliance_from_traces(traces, cstr.rulebook)})
    assert "| m |" in table and "IFR" in table


# ---- repair ----------------------------------------------------------------

def test_sanitizer_redacts_the_hazard_and_keeps_rulebook_facts():
    violations = [
        Violation("AAS-T2-HAZARD-MISSED", "T2", "hazard H-COOLING-LOSS is active, the record..."),
        Violation("AAS-T3-STEP-MISSING", "T3", "required steps skipped: ['verify_coolant_flow']"),
    ]
    lines = sanitize_violations(violations)
    assert not any("H-COOLING-LOSS" in line for line in lines)  # the answer key never leaks
    assert any("verify_coolant_flow" in line for line in lines)  # rulebook facts are open-book
    assert all(line.startswith("AAS-") for line in lines)


class ScriptedProvider(Provider):
    """Real-complete provider that returns scripted texts in order."""

    name = "scripted"

    def __init__(self, texts):
        super().__init__("scripted")
        self._texts = list(texts)

    def complete(self, system, user, max_tokens=4000, temperature=0.0):
        return Completion(text=self._texts.pop(0), model=self.model, provider=self.name)


def test_repair_loop_fixes_a_shallow_failure(cstr):
    case = cstr.case("C01")
    bad = json.dumps({
        "action": "increase_coolant_flow", "params": {"delta_pct": 50},
        "output": {"diagnosis": "cooling loss"},
        "cited_evidence": ["ev_tt101", "ev_ft201"], "checks_performed": [],
        "confidence": 0.9, "reversibility": "undoable",
        "recovery_plan": "restore previous setpoint",
    })
    good = json.dumps({
        "action": "increase_coolant_flow", "params": {"delta_pct": 50},
        "output": {"diagnosis": "cooling loss"},
        "cited_evidence": ["ev_tt101", "ev_ft201", "ev_valve"],
        "checks_performed": [],
        "confidence": 0.9, "reversibility": "undoable",
        "recovery_plan": "restore previous setpoint",
    })
    provider = ScriptedProvider([bad, good])
    first = run_episode(cstr, case, provider)
    assert first.score.aas_code == "AAS-T3-STEP-MISSING"

    outcome, second = run_repair(cstr, case, provider, first)
    assert outcome.repaired and second.score.verdict == "admissible"
    assert outcome.first_code == "AAS-T3-STEP-MISSING"
    assert second.trace["attempt"] == 2
    metrics = repair_metrics([outcome])
    assert metrics["repair_at_1"] == 1.0 and metrics["failures_retried"] == 1


def test_repair_skips_admissible_and_stub_providers(cstr, oracle):
    case = cstr.case("C01")
    first = run_episode(cstr, case, oracle)
    outcome, second = run_repair(cstr, case, oracle, first)
    assert second is None and not outcome.repaired
    assert repair_metrics([outcome])["repair_at_1"] is None


# ---- prompt styles -----------------------------------------------------------

def test_styles_render_distinct_but_share_the_contract(cstr, column):
    for cartridge in (cstr, column):
        case = cartridge.cases[0]
        log, ctx = cartridge.evidence_log_for(case), cartridge.context_for(case)
        systems = {s: render_system(cartridge, style=s) for s in STYLES}
        users = {s: render_user(cartridge, case, log, ctx, style=s) for s in STYLES}
        assert len(set(systems.values())) == len(STYLES)  # genuinely different renderings
        for s in STYLES:
            assert "action record" in systems[s]  # the schema contract is style-invariant
            assert "cited_evidence" in systems[s]
            assert case.evidence[0]["id"] in users[s]
    with pytest.raises(ValueError, match="unknown prompt style"):
        render_system(cstr, style="haiku")


def test_every_style_keeps_the_answer_key_sealed(cstr):
    import copy
    for style in STYLES:
        for case in cstr.cases[:5]:
            log, ctx = cstr.evidence_log_for(case), cstr.context_for(case)
            before = render_user(cstr, case, log, ctx, style=style)
            mutated = copy.deepcopy(case)
            mutated.oracle = {"action": "controlled_shutdown", "params": {}}
            mutated.notes = "CANARY"
            mutated.acceptable_actions = ["canary"]
            after = render_user(cstr, mutated, log, ctx, style=style)
            assert before == after, f"style {style} leaks the answer key"


def test_runner_records_the_style(cstr, oracle):
    result = run_episode(cstr, cstr.case("C08"), oracle, prompt_style="compact")
    assert result.trace["prompt_style"] == "compact"
    assert result.score.verdict == "admissible"  # stub judgment unaffected by rendering


# ---- context stress ----------------------------------------------------------

def test_noise_is_deterministic_disjoint_and_in_trust_map(cstr):
    a = make_noise_evidence(4000, seed=7, before_s=55.0)
    b = make_noise_evidence(4000, seed=7, before_s=55.0)
    assert a == b  # same seed, same haystack
    trust_map = cstr.manifest["trust_map"]
    assert all(e["source"] in trust_map for e in a)
    assert all(e["received_at"] < 55.0 for e in a)
    assert all(e["id"].startswith("ev_noise_") for e in a)


def test_pad_case_grows_context_without_touching_the_key(cstr):
    case = cstr.case("C01")
    padded, noise_ids = pad_case(cstr, case, target_tokens=8000, seed=3)
    assert len(noise_ids) > 20
    original_ids = {e["id"] for e in case.evidence}
    assert original_ids <= {e["id"] for e in padded.evidence}
    assert padded.oracle == case.oracle and padded.load_bearing == case.load_bearing
    total_chars = sum(len(e.get("content", "")) for e in padded.evidence)
    assert total_chars > 6000 * 4 * 0.5  # roughly in the requested regime


def test_oracle_survives_the_haystack_with_full_recall(cstr, oracle):
    point = run_context_point(cstr, cstr.case("C01"), oracle, target_tokens=8000, seed=3)
    assert point.verdict == "admissible"
    assert point.load_bearing_recall == 1.0
    assert point.noise_citations == 0
    assert point.prompt_chars > 8000 * 2  # the haystack actually rendered
