"""The reproducibility artifact builder: manifest, ledger, results, bundle."""

import importlib.util
import json
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location("build_artifact", REPO / "scripts" / "build_artifact.py")
build_artifact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_artifact)


def _gate_trace(model, case, verdict, diagnosed=True):
    return {
        "case_id": case, "provider": "refiant", "model": model,
        "action_record": {"action": "x", "params": {"delta_pct": 50}},
        "ablation": None, "recorded_at": "2026-07-16T07:40:00-0400",
        "cartridge": {"id": "cstr_alpha", "version": "0.1.0"},
        "provenance": {"git_rev": "abc1234", "cartridge_hash": "deadbeef"},
        "sampling": {"temperature": 0.0, "max_tokens": 4000},
        "completion": {"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.0, "latency_s": 2.1},
        "score": {"verdict": verdict, "aas_code": None if verdict == "admissible" else "AAS-T4-ENVELOPE"},
        "gates": {"results": [{"tier": "T2", "info": {"hazard_active": True, "diagnosed": diagnosed}}]},
    }


def test_builder_produces_a_complete_reproducible_bundle(tmp_path):
    run = tmp_path / "run" / "refiant_protea-10" / "gate" / "cstr"
    run.mkdir(parents=True)
    for i, verdict in enumerate(["admissible", "inadmissible", "admissible"]):
        (run / f"C0{i}.json").write_text(json.dumps(_gate_trace("protea-10", f"C0{i}", verdict)))

    out = tmp_path / "artifact"
    build_artifact.main(str(tmp_path / "run"), str(out))

    for name in ("manifest.json", "episode_ledger.csv", "results.json", "traces.tar.gz", "REPRODUCE.md"):
        assert (out / name).exists(), name

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["counts"]["gate_base_episodes"] == 3
    assert manifest["cartridge_content_hashes"] == {"cstr_alpha": "deadbeef"}
    assert manifest["code_git_rev_at_run"] == ["abc1234"]
    assert "No API key" in manifest["secrets_note"]

    results = json.loads((out / "results.json").read_text())
    # 2 of 3 admissible, and the paired table computed over hazard-active outputs
    assert results["gate_admissibility_by_model"]["refiant:protea-10"]["admissible"] == 2
    assert results["paired_diagnosis_action"]["n_hazard_active_parseable"] == 3

    # the bundle unpacks and holds every trace — reproducible from the artifact alone
    with tarfile.open(out / "traces.tar.gz") as tar:
        members = [m.name for m in tar.getmembers() if m.name.endswith(".json")]
    assert len(members) == 3

    ledger = (out / "episode_ledger.csv").read_text().splitlines()
    assert ledger[0].startswith("benchmark,kind,provider,model")
    assert len(ledger) == 4  # header + 3 episodes
