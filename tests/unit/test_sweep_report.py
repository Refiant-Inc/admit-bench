import pytest

from admitbench.report import build_report
from admitbench.runner import run_suite
from admitbench.sweep import (
    aggregate_ci,
    bootstrap_ci,
    pass_rate_ci,
    rank_stability,
    severity_sweep,
    weight_sweep,
)


@pytest.fixture(scope="module")
def suites(cstr):
    from admitbench.providers import get_provider

    return {b: run_suite(cstr, get_provider("stub", b)) for b in ("oracle", "timid", "reckless")}


def test_bootstrap_ci_basics():
    assert bootstrap_ci([]) is None
    flat = bootstrap_ci([0.8] * 10, seed=1)
    # float summation differs across Python versions (3.12 gained compensated
    # summation in sum()); a degenerate distribution is equal within epsilon
    assert flat.mean == pytest.approx(0.8)
    assert flat.lo == pytest.approx(flat.mean) and flat.hi == pytest.approx(flat.mean)
    mixed = bootstrap_ci([0.0, 1.0] * 10, seed=1)
    assert mixed.lo < mixed.mean < mixed.hi
    assert bootstrap_ci([1.0], seed=2).n == 1


def test_suite_confidence_intervals(suites):
    assert pass_rate_ci(suites["oracle"]).mean == 1.0
    assert pass_rate_ci(suites["reckless"]).mean == 0.0
    assert aggregate_ci(suites["reckless"]) is None  # nothing admissible to average
    timid_ci = pass_rate_ci(suites["timid"])
    assert 0.0 < timid_ci.mean < 1.0 and timid_ci.lo <= timid_ci.mean <= timid_ci.hi


def test_weight_sweep_cannot_resurrect_the_inadmissible(suites):
    swept = weight_sweep(suites["reckless"], n_samples=50, seed=3)
    assert swept.default_mean is None and swept.interval is None

    swept = weight_sweep(suites["timid"], n_samples=100, seed=3)
    assert swept.interval.lo <= swept.default_mean <= swept.interval.hi


def test_rank_stability_between_clear_winners(suites):
    stability = rank_stability([suites["oracle"], suites["timid"]], n_samples=100, seed=4)
    assert stability == 1.0  # oracle beats timid under any sane weighting


def test_severity_sweep_shows_the_edges(cstr):
    from admitbench.providers import get_provider

    points = severity_sweep(
        cstr, get_provider("stub", "timid"), "C01", "coolant_flow", [40.0, 70.0, 100.0]
    )
    assert [p.value for p in points] == [40.0, 70.0, 100.0]
    assert points[0].null_crossed_at is not None  # heavily degraded cooling does trip
    assert points[2].null_crossed_at is None      # healthy cooling never does
    assert all(p.verdict for p in points)


def test_report_three_tables(cstr, suites):
    markdown, data = build_report(list(suites.values()), {cstr.id: cstr.rulebook})
    assert "## 1 · Safety failures" in markdown
    assert "## 2 · Safe performance" in markdown
    assert "stub:reckless" in markdown and "AAS-" in markdown
    assert len(data["leaderboard"]) == 3
    assert all(row["verdict"] != "admissible" for row in data["safety_failures"])


def test_report_includes_robustness_when_given(cstr, suites):
    from admitbench.providers import get_provider
    from admitbench.runner import run_cmt_suite

    cmt = run_cmt_suite(cstr, get_provider("stub", "reckless"))
    markdown, data = build_report([suites["reckless"]], {cstr.id: cstr.rulebook}, cmt)
    assert "## 3 · Robustness" in markdown and "mean CMT score" in markdown
    assert data["robustness"]
