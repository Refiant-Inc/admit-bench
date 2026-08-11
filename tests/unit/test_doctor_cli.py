import json
import os

import pytest

from admitbench.cli import main
from admitbench.doctor import FAIL, OK, explain, render_checks, run_checks

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSTR = os.path.join(REPO, "admitbench", "cartridges", "cstr")


def test_doctor_offline_is_healthy():
    checks = run_checks(network=False)
    assert not [c.name for c in checks if c.status == FAIL]
    litmus = next(c for c in checks if "litmus" in c.name)
    assert litmus.status == OK
    text = render_checks(checks)
    assert "doctor" in text and "✓" in text


def test_explain_known_and_unknown_codes():
    text = explain("AAS-T3-STEP-MISSING")
    assert "what it means" in text and "skipped" in text
    assert "Known codes" in explain("AAS-T9-IMAGINARY")
    # partial match expands to the family
    assert "AAS-T4-UNSAFE-DELAY" in explain("t4-unsafe")


def test_cli_validate_ok_and_broken(tmp_path):
    assert main(["validate", CSTR]) == 0
    bad = tmp_path / "empty"
    bad.mkdir()
    assert main(["validate", str(bad)]) == 1


def test_cli_call_and_explain(capsys):
    assert main(["call", "--cartridge", CSTR, "--case", "C01", "--provider", "stub", "--model", "oracle"]) == 0
    out = capsys.readouterr().out
    assert "admissible" in out and "aggregate" in out
    assert main(["explain", "AAS-T0-BAD-JSON"]) == 0


def test_cli_run_writes_report_and_traces(tmp_path, capsys):
    out = tmp_path / "run"
    code = main([
        "run", "--cartridge", CSTR, "--provider", "stub", "--model", "timid",
        "--cases", "C01,C07", "--out", str(out),
    ])
    assert code == 0
    report = json.loads((out / "report.json").read_text())
    assert report["confidence"]["pass_rate"] is not None
    assert (out / "C01.json").exists() and (out / "C07.json").exists()
    trace = json.loads((out / "C07.json").read_text())
    assert trace["score"]["aggregate"] is None  # timid fails the imminent case
    assert trace["ledger_head"] and trace["gates"]["results"]


def test_cli_sweep_and_cmt_and_prompt(capsys):
    assert main(["sweep", "--cartridge", CSTR, "--provider", "stub", "--model", "oracle", "--samples", "20"]) == 0
    assert "pass rate" in capsys.readouterr().out
    assert main(["cmt", "--cartridge", CSTR, "--provider", "stub", "--model", "oracle"]) == 0
    assert "caution monotonicity" in capsys.readouterr().out
    assert main(["prompt"]) == 0
    assert "manifest.yaml" in capsys.readouterr().out


def test_cli_build_and_ingest_and_promote(tmp_path, capsys):
    source = tmp_path / "notes.txt"
    source.write_text("TT-900 trips at 400 K. Loss of cooling causes runaway.\n1. verify flow\n")
    out = tmp_path / "draft"
    assert main(["build", str(source), "--out", str(out), "--id", "demo", "--world", "cstr"]) == 0
    assert (out / "BUILD_REPORT.md").exists()

    log = tmp_path / "ops.txt"
    log.write_text("Strainer blockage causes low coolant flow.")
    assert main(["ingest", "--cartridge", str(out), "--log", str(log), "--by", "tests"]) == 0
    assert main(["promote", "--cartridge", str(out), "--entry", "CE-OP-001", "--to", "reviewed"]) == 0


def test_cli_init_yes_writes_config(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REFIANT_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["init", "--yes"]) == 0
    config = json.loads((tmp_path / "admitbench.config.json").read_text())
    assert config["provider"] == "stub"
    out = capsys.readouterr().out
    assert "doctor" in out and "Safe performance" in out  # ran the checkup and the demo


def test_cli_errors_return_nonzero(capsys):
    assert main(["call", "--cartridge", CSTR, "--case", "NOPE"]) == 1
    assert "error:" in capsys.readouterr().err
