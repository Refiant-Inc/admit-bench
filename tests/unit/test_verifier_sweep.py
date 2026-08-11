"""The verifier sensitivity sweep — does a T4 verdict survive moving the line?

The properties that matter: scale 1.0 must reproduce the shipped verifier
exactly (otherwise the sweep is measuring a different bench), loosening must be
monotone (a limit further from nominal can never create a violation), and
nothing here may mutate the world it was handed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from admitbench.cartridge import load_cartridge
from admitbench.physics import world_for
from admitbench.verifier_sweep import (
    DEFAULT_SCALES,
    MARGINAL_BAND,
    CaseSensitivity,
    VerifierSweep,
    perturbed_world,
    sweep_case,
    sweep_run,
    sweep_table,
)

CARTRIDGES = Path(__file__).resolve().parents[2] / "admitbench" / "cartridges"


@pytest.fixture(scope="module")
def cstr():
    return load_cartridge(CARTRIDGES / "cstr")


# ------------------------------------------------------------ the perturbation


def test_scale_one_reproduces_the_shipped_envelope(cstr):
    world = world_for("cstr")
    assert perturbed_world(world, 1.0).envelope() == world.envelope()


def test_loosening_moves_the_limit_away_from_nominal():
    world = world_for("cstr")
    base = world.envelope()["T"]
    loose = perturbed_world(world, 1.5).envelope()["T"]
    tight = perturbed_world(world, 0.5).envelope()["T"]
    # CSTR T limit is a max above nominal
    assert loose["limit"] > base["limit"] > tight["limit"]
    assert loose["nominal"] == base["nominal"], "nominal must not move"


def test_perturbation_does_not_mutate_the_original_world():
    world = world_for("cstr")
    before = world.envelope()
    perturbed_world(world, 2.0).envelope()
    assert world.envelope() == before


def test_a_non_positive_scale_is_refused():
    with pytest.raises(ValueError):
        perturbed_world(world_for("cstr"), 0.0)


# ------------------------------------------------------------------ behaviour


def test_an_insufficient_action_is_rejected_and_stays_rejected_when_tightened(cstr):
    """C07 is a known T4 failure. Tightening must not rescue it."""
    result = sweep_case(cstr, "C07", "increase_coolant_flow", {"delta_pct": 10})
    assert not result.error
    assert result.baseline_crossed is True
    for scale in (s for s in DEFAULT_SCALES if s <= 1.0):
        assert result.crossed_by_scale[scale] is True, f"tightening to {scale} rescued it"


def test_loosening_is_monotone(cstr):
    """Once an action stops violating, it must not start again as limits loosen."""
    result = sweep_case(cstr, "C07", "increase_coolant_flow", {"delta_pct": 10})
    ordered = [result.crossed_by_scale[s] for s in sorted(result.crossed_by_scale)]
    # crossed=True may become False as scale rises, never the reverse
    assert ordered == sorted(ordered, reverse=True), ordered


def test_a_safe_action_is_stable(cstr):
    result = sweep_case(cstr, "C01", "increase_coolant_flow", {"delta_pct": 20})
    assert not result.error
    assert result.baseline_crossed is False
    assert result.stable is True
    assert result.flip_scale is None


def test_an_unprojectable_action_is_an_error_not_a_pass(cstr):
    result = sweep_case(cstr, "C01", "no_such_action", {})
    # either it errors, or it is judged — but it must never be silently "safe
    # because we could not tell"
    assert result.error or isinstance(result.baseline_crossed, bool)
    if result.error:
        assert result.stable is False


def test_marginal_means_close_to_the_shipped_setting():
    near = CaseSensitivity("C1", "m", "a", True, flip_scale=1.0 + MARGINAL_BAND / 2)
    far = CaseSensitivity("C2", "m", "a", True, flip_scale=1.0 + MARGINAL_BAND * 3)
    assert near.marginal and not far.marginal


# --------------------------------------------------------------- run + report


def test_sweep_run_reads_base_episodes_only(tmp_path):
    """CMT ablations and reports must not be swept as if they were episodes."""
    import json

    base = {
        "case_id": "C01", "model": "m", "cartridge": {"id": "cstr_alpha"},
        "action_record": {"action": "increase_coolant_flow", "params": {"delta_pct": 20}},
    }
    (tmp_path / "C01.json").write_text(json.dumps(base))
    (tmp_path / "C01+A1.json").write_text(json.dumps({**base, "ablation": "A1"}))
    (tmp_path / "cmt_C01.json").write_text(json.dumps(base))
    (tmp_path / "report.json").write_text(json.dumps({"not": "a trace"}))

    sweep = sweep_run(tmp_path)
    assert len(sweep.cases) == 1 and sweep.cases[0].case_id == "C01"


def test_sweep_run_finds_traces_nested_under_model_directories(tmp_path):
    """A real multi-model run nests <model>/<suite>/<cartridge>/<case>.json.

    Reading only the top level would report an empty sweep instead of failing,
    which is worse than an error: it looks like a clean result.
    """
    import json

    for model in ("model_a", "model_b"):
        deep = tmp_path / model / "safetybench" / "cstr_alpha"
        deep.mkdir(parents=True)
        (deep / "C01.json").write_text(
            json.dumps({
                "case_id": "C01", "model": model, "cartridge": {"id": "cstr_alpha"},
                "action_record": {"action": "increase_coolant_flow", "params": {"delta_pct": 20}},
            })
        )

    sweep = sweep_run(tmp_path)
    assert len(sweep.cases) == 2
    assert {c.model for c in sweep.cases} == {"model_a", "model_b"}


def test_a_trace_with_no_record_is_skipped(tmp_path):
    import json

    (tmp_path / "C05.json").write_text(
        json.dumps({"case_id": "C05", "cartridge": {"id": "cstr_alpha"}, "action_record": None})
    )
    assert sweep_run(tmp_path).cases == []


def test_summaries_are_none_rather_than_zero_when_there_is_nothing_to_report():
    empty = VerifierSweep(scales=(1.0,), horizons_s=(1200.0,))
    assert empty.stability() is None
    assert empty.decisive_rejection_rate() is None


def test_table_renders_and_names_the_shipped_setting(cstr):
    sweep = VerifierSweep(scales=DEFAULT_SCALES, horizons_s=(1200.0,))
    sweep.cases.append(sweep_case(cstr, "C07", "increase_coolant_flow", {"delta_pct": 10}))
    text = sweep_table(sweep)
    assert "Verifier sensitivity" in text
    assert "1.0 is the shipped verifier" in text


def test_the_sweep_never_writes_to_the_run_directory(tmp_path):
    import json

    (tmp_path / "C01.json").write_text(
        json.dumps({
            "case_id": "C01", "cartridge": {"id": "cstr_alpha"},
            "action_record": {"action": "increase_coolant_flow", "params": {"delta_pct": 20}},
        })
    )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    sweep_run(tmp_path)
    after = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert before == after
