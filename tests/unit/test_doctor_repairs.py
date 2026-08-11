"""The repair ladder: safe fixes heal, guarded fixes wait for --fix,
kernel integrity is never auto-fixed, and a gutted checkup never says all clear."""

import json
import shutil
from pathlib import Path

from admitbench import repairs as rep
from admitbench.doctor import FAIL, OK, WARN, overall, render_checks, run_checks

REPO = Path(__file__).resolve().parent.parent.parent


def find(checks, prefix):
    return [c for c in checks if c.name.startswith(prefix)]


# ---- safe repairs heal and verify against a re-run ---------------------------

def test_corrupt_config_is_rebuilt_with_backup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("admitbench.config.json").write_text("{definitely not json")
    check = find(run_checks(cartridge_paths=[]), "config file parses")[0]
    assert check.status == FAIL and check.repair.kind == rep.SAFE
    note = check.repair.apply()
    assert "rebuilt" in note
    assert Path("admitbench.config.json.bak").exists()  # the corrupt original survives
    again = find(run_checks(cartridge_paths=[]), "config file parses")[0]
    assert again.status == OK  # the repair verified itself


def test_tmp_litter_removed_and_corrupt_traces_quarantined(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = Path("runs"); runs.mkdir()
    (runs / "C01.json.tmp").write_text("half a wri")
    (runs / "broken.json").write_text('{"case_id": "C01", "score"')
    (runs / "fine.json").write_text('{"ok": true}')

    checks = run_checks(cartridge_paths=[])
    litter = find(checks, "no interrupted-write litter")[0]
    corrupt = find(checks, "all traces under runs/ parse")[0]
    assert litter.status == WARN and corrupt.status == WARN
    litter.repair.apply()
    corrupt.repair.apply()

    assert not (runs / "C01.json.tmp").exists()
    assert not (runs / "broken.json").exists()
    quarantined = list((runs / "_corrupt").iterdir())
    assert len(quarantined) == 1  # quarantined, never deleted
    assert (runs / "fine.json").exists()  # untouched
    again = run_checks(cartridge_paths=[])
    assert find(again, "runs/ hygiene")[0].status == OK


def test_env_skeleton_never_touches_an_existing_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for env in ("REFIANT_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    shutil.copy(REPO / ".env.example", ".env.example")

    check = find(run_checks(cartridge_paths=[]), "provider keys")[0]
    assert check.repair.kind == rep.SAFE
    check.repair.apply()
    assert Path(".env").exists()

    Path(".env").write_text("REFIANT_API_KEY=real-secret\n")
    assert "left untouched" in check.repair.apply()  # idempotent, secrets never overwritten
    assert "real-secret" in Path(".env").read_text()


# ---- guarded repairs on cartridge files ---------------------------------------

def test_broken_weights_get_a_guarded_normalize_repair(tmp_path, monkeypatch):
    import yaml

    cart = tmp_path / "cart"
    shutil.copytree(REPO / "admitbench" / "cartridges" / "cstr", cart)
    manifest = yaml.safe_load((cart / "manifest.yaml").read_text())
    manifest["scoring"]["weights"]["T4"] = 0.9  # sums to 1.6
    (cart / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    monkeypatch.chdir(tmp_path)

    checks = run_checks(cartridge_paths=[cart])
    broken = [c for c in find(checks, "cartridge") if c.status == FAIL]
    guarded = [c for c in broken if c.repair and c.repair.kind == rep.GUARDED]
    assert guarded, "weight-sum errors must offer a guarded normalize repair"
    guarded[0].repair.apply()
    assert (cart / "manifest.yaml.bak").exists()

    again = run_checks(cartridge_paths=[cart])
    assert any(c.status == OK for c in find(again, "cartridge")), "repair must verify"


def test_unrecognized_cartridge_errors_escalate_not_autofix(tmp_path, monkeypatch):
    cart = tmp_path / "cart"
    shutil.copytree(REPO / "admitbench" / "cartridges" / "cstr", cart)
    # an oracle naming an action outside the grammar needs judgment, not a patch
    lines = (cart / "procedures_cases.jsonl").read_text().splitlines()
    patched = [
        line.replace('"action": "increase_coolant_flow"', '"action": "summon_wizard"', 1)
        if '"id": "C01"' in line else line
        for line in lines
    ]
    (cart / "procedures_cases.jsonl").write_text("\n".join(patched) + "\n")
    monkeypatch.chdir(tmp_path)
    checks = run_checks(cartridge_paths=[cart])
    broken = [c for c in find(checks, "cartridge") if c.status == FAIL]
    assert broken
    kinds = {c.repair.kind for c in broken if c.repair}
    assert rep.ESCALATE in kinds
    assert rep.SAFE not in kinds  # nothing here is silently patchable


# ---- kernel integrity and the honest summary -----------------------------------

def test_kernel_checks_are_never_auto_fixable():
    checks = run_checks()
    for prefix in ("physics", "litmus", "answer key sealed", "end-to-end"):
        for c in find(checks, prefix):
            assert c.repair is None or c.repair.kind == rep.ESCALATE, (
                f"{c.name} must never carry an auto-repair"
            )


def test_gutted_checkup_never_says_all_clear(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no cartridges anywhere
    checks = run_checks(cartridge_paths=[])
    summary, code = overall(checks)
    assert code == 1
    assert "all clear" not in summary
    rendered = render_checks(checks)
    assert "do NOT treat this install as verified" in rendered or "failure" in rendered


def test_healthy_repo_still_says_all_clear():
    summary, code = overall(run_checks())
    assert code == 0 and summary == "all clear — the bench is trustworthy"


def test_missing_cartridges_is_fail_with_escalation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    check = find(run_checks(cartridge_paths=[]), "bundled cartridges")[0]
    assert check.status == FAIL and check.repair.kind == rep.ESCALATE
    assert "clone" in check.repair.description


# ---- the --fix loop -------------------------------------------------------------

def test_doctor_fix_cli_heals_a_broken_workspace(tmp_path, monkeypatch, capsys):
    from admitbench.cli import main

    monkeypatch.chdir(tmp_path)
    Path("admitbench.config.json").write_text("{broken")
    runs = Path("runs"); runs.mkdir()
    (runs / "x.json.tmp").write_text("litter")
    # cartridges resolve via the package fallback (repo checkout), so core runs
    code = main(["doctor", "--fix"])
    out = capsys.readouterr().out
    assert "applying repairs:" in out and "rebuilt" in out
    assert not (runs / "x.json.tmp").exists()
    assert json.loads(Path("admitbench.config.json").read_text())  # valid again
    assert code == 0 and "all clear" in out