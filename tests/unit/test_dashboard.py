"""The dashboard reads traces, never re-scores them, and survives litter."""

import json
from pathlib import Path

import pytest

from admitbench.dashboard import collect, render_dashboard, summarize, write_dashboard
from admitbench.providers import get_provider
from admitbench.runner import run_suite


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    from admitbench.cartridge import load_cartridge

    cstr = load_cartridge(Path(__file__).resolve().parents[2] / "admitbench" / "cartridges" / "cstr")
    out = tmp_path_factory.mktemp("runs")
    run_suite(cstr, get_provider("stub", "oracle"), case_ids=["C01", "C02", "C05"],
              out_dir=out / "oracle")
    run_suite(cstr, get_provider("stub", "reckless"), case_ids=["C01", "C02"],
              out_dir=out / "reckless")
    (out / "broken.json").write_text("{not json")            # litter must not crash it
    (out / "report.json").write_text('{"score": "decoy"}')   # reports are not episodes
    return out


def test_collect_finds_every_episode_and_skips_litter(run_dir):
    rows = collect(run_dir)
    assert len(rows) == 5
    assert {r["case"] for r in rows} == {"C01", "C02", "C05"}
    assert all(r["kind"] == "base" for r in rows)
    assert all(r["rev"] for r in rows)  # provenance made it through


def test_summary_matches_the_verdicts_not_a_rescoring(run_dir):
    stats = summarize(collect(run_dir))
    oracle = stats["stub:oracle"]
    assert oracle["episodes"] == 3 and oracle["pass_rate"] == 1.0
    reckless = stats["stub:reckless"]
    assert reckless["pass_rate"] == 0.0  # the dashboard reports, it never rescues


def test_dashboard_html_is_self_contained(run_dir):
    path = write_dashboard(run_dir)
    text = path.read_text()
    assert path.name == "dashboard.html"
    # interactive McKinsey dashboard: embedded data, exhibits, selectors, trace audit
    for marker in ('"model":"oracle"', "const DATA", "Exhibit 1", "Admissibility",
                   "Trace audit", "tok_in", "prefers-color-scheme", "<svg", "chip"):
        assert marker in text, marker
    # self-contained: a strict CSP page must need no network at all
    assert "http://" not in text and "https://" not in text and "<script src" not in text


def test_dashboard_escapes_script_breakout_in_embedded_json():
    """Model/trace fields must not close the inline script via </script>."""
    from admitbench.dashboard_ui import render_dashboard

    rows = [{
        "run_top": "x", "run": "x", "cartridge": "cstr", "case": "C01", "kind": "base",
        "provider": "stub", "model": "</script><script>alert(1)//", "verdict": "admissible",
        "code": None, "aggregate": 1.0, "confidence": 0.9, "tok_in": 0, "tok_out": 0,
        "cost": 0.0, "latency": 0.0, "truncated": False, "rev": "test", "hazard": None,
        "diag": "ok", "action": "escalate", "reversibility": "undoable", "_k": "k",
    }]
    html_out = render_dashboard(rows, title="test")
    assert "</script><script>" not in html_out
    assert "\\u003c/script\\u003e" in html_out


def test_dashboard_counts_repair_and_ablated_separately(run_dir, tmp_path):
    trace = json.loads(next((run_dir / "reckless").glob("C01.json")).read_text())
    trace["attempt"] = 2
    (run_dir / "reckless" / "C01.repair.json").write_text(json.dumps(trace))
    rows = collect(run_dir)
    kinds = {r["kind"] for r in rows}
    assert "repair" in kinds
    # base-only stats are unchanged by the repair trace
    assert summarize(rows)["stub:reckless"]["episodes"] == 2


def test_empty_directory_is_an_error_not_an_empty_page(tmp_path):
    with pytest.raises(FileNotFoundError):
        write_dashboard(tmp_path)


def test_run_cli_emits_a_dashboard(tmp_path, capsys):
    from admitbench.cli import main

    out = tmp_path / "run"
    assert main(["run", "--provider", "stub", "--model", "oracle",
                 "--cartridge", "cstr", "--cases", "C01", "--out", str(out)]) == 0
    assert (out / "dashboard.html").exists()
    assert "dashboard: open" in capsys.readouterr().out


def test_dashboard_cli_aggregates_across_runs(run_dir, capsys):
    from admitbench.cli import main

    assert main(["dashboard", str(run_dir)]) == 0
    printed = capsys.readouterr().out
    assert "model×style lanes" in printed and "total spend" in printed


# ---- Layer-1 dashboard: a different benchmark, its own view --------------------

def _l1_trace(sid, cls, cartridge="cstr_101", severity="high"):
    score = {"correct_safe": 1.0, "incorrect_safe": 0.5}[cls]
    return {
        "benchmark_layer": "layer1_action_selection", "scenario_id": sid,
        "cartridge": cartridge, "fault_family": "x",
        "variant_factors": {"severity": severity, "production_pressure": "none",
                            "constraint_visibility": "explicit"},
        "provider": "openrouter", "model": "anthropic/claude-haiku-4.5",
        "selected_action": "A" if cls == "correct_safe" else "B",
        "matrix_class": cls, "score": score, "safe": True,
        "correct": cls == "correct_safe", "hard_violation": False,
        "completion": {"latency_s": 1.2, "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.001},
    }


def test_layer1_dashboard_builds_and_excludes_gate_traces(tmp_path):
    import json as _json

    from admitbench.dashboard import collect, collect_layer1, write_layer1_dashboard

    l1 = tmp_path / "openrouter_x" / "layer1"
    l1.mkdir(parents=True)
    for i, cls in enumerate(["correct_safe"] * 5 + ["incorrect_safe"] * 2):
        (l1 / f"S{i}.json").write_text(_json.dumps(_l1_trace(f"S{i}", cls)), encoding="utf-8")
    (l1 / "_summary.json").write_text('{"n": 7}')  # summary is not an episode

    rows = collect_layer1(tmp_path)
    assert len(rows) == 7
    assert collect(tmp_path) == []  # layer1 traces never leak into the gate dashboard

    path = write_layer1_dashboard(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert path.name == "layer1_dashboard.html"
    for marker in ("ADMIT Bench — Layer-1", "0", "unsafe selections", "safe-fallback",
                   "Outcome composition", "correct safe recovery"):
        assert marker in text
    assert "http://" not in text  # self-contained


def test_layer1_dashboard_absent_when_no_layer1_traces(tmp_path):
    from admitbench.dashboard import write_layer1_dashboard

    assert write_layer1_dashboard(tmp_path) is None
