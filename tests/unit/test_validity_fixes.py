"""The validity fixes: structured T2, negation-aware matching, the two-token
floor, T4 feedback redaction, noise/lookup isolation, provenance, repair
resume, and consistency@k."""

import json
from pathlib import Path

import pytest

from admitbench.cartridge import validate_cartridge
from admitbench.gates import _diagnosis_matches
from admitbench.record import ActionRecord

REPO = Path(__file__).resolve().parent.parent.parent


def _rec(output: dict) -> ActionRecord:
    return ActionRecord(
        action="hold_and_monitor", confidence=0.8, reversibility="undoable",
        cited_evidence=[], checks_performed=[], output=output,
    )


# ---- the matcher: structured first, negation-aware fallback -------------------

def test_structured_hazard_id_matches_exactly():
    rec = _rec({"hazard_id": "H-COOLING-LOSS"})
    assert _diagnosis_matches(rec, [], hazard_id="H-COOLING-LOSS")
    assert not _diagnosis_matches(rec, [], hazard_id="H-RUNAWAY")


def test_structured_none_falls_through_to_text():
    rec = _rec({"hazard_id": "none", "diagnosis": "clear cooling loss on the jacket"})
    assert _diagnosis_matches(rec, ["cooling loss"], hazard_id="H-COOLING-LOSS")


def test_negated_diagnosis_does_not_match():
    rec = _rec({"diagnosis": "no cooling loss found; sensors nominal"})
    assert not _diagnosis_matches(rec, ["cooling loss"], hazard_id="H-COOLING-LOSS")


def test_negation_elsewhere_does_not_poison_a_real_match():
    rec = _rec({"diagnosis": "cooling loss confirmed, though not yet at trip"})
    assert _diagnosis_matches(rec, ["cooling loss"], hazard_id="H-COOLING-LOSS")


def test_shipped_cartridges_pass_the_two_token_floor():
    for name in ("cstr", "distillation"):
        errors, _ = validate_cartridge(REPO / "admitbench" / "cartridges" / name)
        assert not errors


def test_single_token_accept_phrase_fails_validation(tmp_path):
    import shutil
    cart = tmp_path / "cart"
    shutil.copytree(REPO / "admitbench" / "cartridges" / "cstr", cart)
    lines = (cart / "procedures_cases.jsonl").read_text().splitlines()
    out = []
    for line in lines:
        if '"id": "C02"' in line and '"kind": "case"' in line:
            d = json.loads(line)
            d["diagnosis_accept"].append("coolant")
            line = json.dumps(d)
        out.append(line)
    (cart / "procedures_cases.jsonl").write_text("\n".join(out) + "\n")
    errors, _ = validate_cartridge(cart)
    assert any("fewer than 2 tokens" in e for e in errors)


# ---- T4 redaction: no crossing time reaches the repair channel ----------------

def test_t4_feedback_never_carries_the_crossing_time():
    from admitbench.checker import Violation
    from admitbench.repair import sanitize_violations

    raw = Violation(
        "AAS-T4-UNSAFE-DELAY", "T4",
        "hold leaves the plant unattended while the null trajectory crosses "
        "the envelope at t=540s, inside the 300s response window",
    )
    line = sanitize_violations([raw])[0]
    assert "540" not in line and "t=" not in line
    assert line.startswith("AAS-T4-UNSAFE-DELAY:")


def test_non_t2_t4_violations_pass_verbatim():
    from admitbench.checker import Violation
    from admitbench.repair import sanitize_violations

    raw = Violation("AAS-T3-SKIPPED-STEP", "T3", "required step confirm_two_sensors was skipped")
    assert sanitize_violations([raw])[0].endswith("confirm_two_sensors was skipped")


# ---- context noise never feeds the hypothesis lookup --------------------------

def test_noise_evidence_is_excluded_from_hypotheses(cstr):
    from admitbench.context_stress import pad_case
    from admitbench.hypotheses import candidate_hypotheses

    case = cstr.case("C01")
    padded, noise_ids = pad_case(cstr, case, target_tokens=8000)
    assert noise_ids
    base = candidate_hypotheses(cstr, cstr.evidence_log_for(case), cstr.context_for(case))
    noisy = candidate_hypotheses(cstr, cstr.evidence_log_for(padded), cstr.context_for(padded))
    assert [h.ce_id for h in base] == [h.ce_id for h in noisy]
    assert [round(h.score, 6) for h in base] == [round(h.score, 6) for h in noisy]


# ---- provenance: every trace says which code and which cartridge ran ----------

def test_trace_carries_git_rev_and_cartridge_hash(cstr, oracle):
    from admitbench.runner import run_episode

    trace = run_episode(cstr, cstr.case("C01"), oracle).trace
    prov = trace["provenance"]
    assert prov["cartridge_hash"] == cstr.content_hash()
    assert len(prov["cartridge_hash"]) == 16
    assert prov["git_rev"]  # "unknown" outside a checkout is an honest answer


def test_cartridge_hash_changes_when_a_file_changes(tmp_path):
    import shutil
    from admitbench.cartridge import load_cartridge

    cart = tmp_path / "cart"
    shutil.copytree(REPO / "admitbench" / "cartridges" / "cstr", cart)
    before = load_cartridge(cart).content_hash()
    with open(cart / "manifest.yaml", "a") as f:
        f.write("\n# annotation\n")
    assert load_cartridge(cart).content_hash() != before


# ---- repair: resume-restored episodes replay their gates ----------------------

def test_run_repair_survives_a_resumed_episode(cstr, monkeypatch):
    """A trace-restored result has gate_report=None; repair must replay the
    gates instead of crashing."""
    from admitbench.providers import get_provider
    from admitbench.repair import run_repair
    from admitbench.runner import _result_from_trace, run_episode

    case = cstr.case("C01")
    reckless = get_provider("stub", "reckless")
    first = run_episode(cstr, case, reckless)
    assert first.score.verdict != "admissible"
    restored = _result_from_trace(first.trace)
    assert restored.gate_report is None

    class OneShot:
        name, model = "test", "one-shot"
        def complete(self, system, user, **kw):
            oracle = get_provider("stub", "oracle")
            log, ctx = cstr.evidence_log_for(case), cstr.context_for(case)
            text = oracle.play(cstr, case, log, ctx)
            assert "t=" not in user.split("Gate violations")[-1].split("Emit ONE")[0]
            from admitbench.providers import Completion
            return Completion(text=text, model=self.model, provider=self.name)

    outcome, second = run_repair(cstr, case, OneShot(), restored)
    assert outcome.second_verdict == "admissible" and outcome.repaired
    assert second is not None and second.trace["attempt"] == 2


# ---- consistency@k -------------------------------------------------------------

def test_consistency_at_k_flags_flicker():
    from admitbench.sweep import consistency_at_k

    class R:
        def __init__(self, cid, verdict):
            self.case_id = cid
            self.score = type("S", (), {"verdict": verdict})()

    class Suite:
        def __init__(self, results):
            self.results = results

    suites = [
        Suite([R("C01", "admissible"), R("C02", "inadmissible")]),
        Suite([R("C01", "admissible"), R("C02", "admissible")]),
    ]
    out = consistency_at_k(suites)
    assert out["k"] == 2 and out["cases"] == 2
    assert out["consistent_pass"] == 1 and out["flicker_cases"] == ["C02"]
    assert out["consistency"] == 0.5


# ---- run --repair is wired ------------------------------------------------------

def test_cli_run_repair_flag_reports_repair_metrics(tmp_path, capsys):
    from admitbench.cli import main

    out = tmp_path / "run"
    code = main([
        "run", "--cartridge", str(REPO / "admitbench" / "cartridges" / "cstr"),
        "--provider", "stub", "--model", "reckless",
        "--cases", "C01,C02", "--repair", "--out", str(out),
    ])
    assert code == 0
    printed = capsys.readouterr().out
    assert "Repair loop" in printed
    # stub providers are skipped inside run_repair by design, so the metric
    # reports zero retries — the flag, the loop, and the report section are live
    assert "failures retried: 0" in printed
