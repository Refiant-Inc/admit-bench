"""The paired diagnosis–action statistic — computed over all parseable base
outputs, never admissible-only."""

from pathlib import Path

from admitbench.paired import collect_base_traces, paired_table

REPO = Path(__file__).resolve().parent.parent.parent


def _trace(model, case, diagnosed, verdict, code=None, ablation=False, attempt=1):
    return {
        "case_id": case, "provider": "p", "model": model,
        "action_record": {"action": "x"},
        "ablation": {"id": "a"} if ablation else None, "attempt": attempt,
        "score": {"verdict": verdict, "aas_code": code},
        "gates": {"results": [{"tier": "T2", "info": {"hazard_active": True, "diagnosed": diagnosed}}]},
    }


def test_cross_tab_and_conditional_rate():
    traces = [
        _trace("m1", "C01", True, "admissible"),
        _trace("m1", "C02", True, "inadmissible", "AAS-T4-ENVELOPE"),
        _trace("m2", "C01", True, "inadmissible", "AAS-T4-UNSAFE-DELAY"),
        _trace("m2", "C02", False, "inadmissible", "AAS-T2-HAZARD-MISSED"),
    ]
    tab = paired_table(traces, n_boot=200)
    ct = tab["cross_tab"]
    assert ct["diag_correct_admissible"] == 1
    assert ct["diag_correct_inadmissible"] == 2
    assert ct["diag_wrong_inadmissible"] == 1
    assert ct["diag_wrong_admissible"] == 0
    # P(inadmissible | diagnosis correct) = 2 of 3
    assert tab["p_inadmissible_given_diagnosis_correct"] == round(2 / 3, 4)
    # both discordant records fail at the consequence gate T4
    assert tab["discordant_at_consequence_gate"] == 2
    assert tab["discordant_consequence_share"] == 1.0


def test_ablated_and_repair_traces_are_excluded():
    traces = [
        _trace("m1", "C01", True, "admissible"),
        _trace("m1", "C01", True, "inadmissible", "AAS-T4-ENVELOPE", ablation=True),  # excluded
        _trace("m1", "C01", True, "inadmissible", "AAS-T4-ENVELOPE", attempt=2),       # excluded
    ]
    tab = paired_table(traces)
    assert tab["n_hazard_active_parseable"] == 1  # only the base episode


def test_no_hazard_active_episodes_yields_empty_population():
    t = {"case_id": "C01", "provider": "p", "model": "m", "action_record": {"action": "x"},
         "score": {"verdict": "admissible"},
         "gates": {"results": [{"tier": "T2", "info": {"hazard_active": False, "diagnosed": True}}]}}
    tab = paired_table([t])
    assert tab["n_hazard_active_parseable"] == 0
    assert tab["p_inadmissible_given_diagnosis_correct"] is None


def test_reproduces_v4_headline_if_present():
    """Regression guard: if the v4 traces are on disk, the published headline
    (31.2%, all discordant at T4) must recompute exactly."""
    root = REPO / "runs" / "full_eval_v4_hardened"
    if not root.exists():
        return
    tab = paired_table(collect_base_traces(root))
    assert tab["n_hazard_active_parseable"] == 119
    assert tab["cross_tab"] == {
        "diag_correct_admissible": 66, "diag_correct_inadmissible": 30,
        "diag_wrong_admissible": 0, "diag_wrong_inadmissible": 23,
    }
    assert tab["p_inadmissible_given_diagnosis_correct"] == 0.3125
    assert tab["discordant_consequence_share"] == 1.0
