"""The T0 compile stage: a cartridge that does not validate cannot benchmark."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from admitbench.cartridge import CartridgeError, load_cartridge, validate_cartridge

REPO = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def broken(tmp_path):
    """A mutable copy of the CSTR cartridge plus helpers to break it."""
    path = tmp_path / "cart"
    shutil.copytree(REPO / "admitbench" / "cartridges" / "cstr", path)

    class Kit:
        root = path

        @staticmethod
        def manifest(mutate):
            data = yaml.safe_load((path / "manifest.yaml").read_text())
            mutate(data)
            (path / "manifest.yaml").write_text(yaml.safe_dump(data, sort_keys=False))

        @staticmethod
        def jsonl(name, mutate):
            entries = [json.loads(l) for l in (path / name).read_text().splitlines() if l.strip()]
            mutate(entries)
            (path / name).write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        @staticmethod
        def errors():
            errors, _ = validate_cartridge(path)
            return "\n".join(errors)

    return Kit()


def test_shipped_cartridges_compile(cstr, column):
    assert len(cstr.cases) == 15 and len(column.cases) == 10
    assert cstr.world().name == "cstr" and column.world().name == "column"


def test_missing_file(broken):
    (broken.root / "system_graph.jsonl").unlink()
    assert "missing file" in broken.errors()


def test_invalid_yaml_and_invalid_jsonl(broken, tmp_path):
    (broken.root / "manifest.yaml").write_text("id: [unclosed")
    assert "invalid YAML" in broken.errors()

    path2 = tmp_path / "cart2"
    shutil.copytree(REPO / "admitbench" / "cartridges" / "cstr", path2)
    (path2 / "system_graph.jsonl").write_text('{"kind": "tag", broken\n')
    errors, _ = validate_cartridge(path2)
    assert any("invalid JSON" in e for e in errors)


def test_missing_required_manifest_keys(broken):
    broken.manifest(lambda m: m.pop("trust_map"))
    assert "trust_map" in broken.errors()


def test_unknown_world(broken):
    broken.manifest(lambda m: m.update(world="tokamak"))
    assert "unknown world" in broken.errors()


def test_empty_authority_scope(broken):
    broken.manifest(lambda m: m["agent"].update(authority_scope=[]))
    assert "authority_scope" in broken.errors()


def test_weights_must_sum_to_one(broken):
    broken.manifest(lambda m: m["scoring"]["weights"].update(T4=0.9))
    assert "sum" in broken.errors()


def test_bad_action_grammar(broken):
    broken.manifest(lambda m: m["actions"].append({"action": "x", "class": "yolo", "reversibility": "undoable"}))
    assert "bad action grammar" in broken.errors()


def test_case_referencing_unknown_pieces(broken):
    def mutate(entries):
        case = next(e for e in entries if e.get("id") == "C01")
        case["oracle"]["action"] = "summon_wizard"
        case["hazard_id"] = "H-GOBLINS"
        case["load_bearing"] = ["ev_nope"]
        case["evidence"][0]["source"] = "carrier_pigeon"
        case["evidence"][1]["tag"] = "XX-999"

    broken.jsonl("procedures_cases.jsonl", mutate)
    errors = broken.errors()
    for fragment in ("summon_wizard", "H-GOBLINS", "ev_nope", "carrier_pigeon", "XX-999"):
        assert fragment in errors, fragment


def test_oracle_must_itself_be_admissible(broken):
    def mutate(entries):
        case = next(e for e in entries if e.get("id") == "C01")
        # drop the FT-201 evidence the commit oracle requires
        case["evidence"] = [e for e in case["evidence"] if e["id"] != "ev_ft201"]
        case["load_bearing"] = ["ev_tt101"]
        case["ablations"] = []

    broken.jsonl("procedures_cases.jsonl", mutate)
    assert "the oracle itself would be inadmissible" in broken.errors()


def test_duplicate_case_and_evidence_ids(broken):
    def mutate(entries):
        case = next(e for e in entries if e.get("id") == "C01")
        entries.append(dict(case))  # duplicate case id
        case["evidence"].append(dict(case["evidence"][0]))  # duplicate evidence id

    broken.jsonl("procedures_cases.jsonl", mutate)
    errors = broken.errors()
    assert "duplicate case id" in errors and "duplicate evidence id" in errors


def test_bad_review_status_and_candidate_warning(broken):
    broken.jsonl("safety_case_graph.jsonl", lambda es: es[0].update(review_status="vibes"))
    assert "bad review_status" in broken.errors()

    fresh_errors, warnings = validate_cartridge(REPO / "admitbench" / "cartridges" / "cstr")
    assert not fresh_errors
    assert any("candidate" in w for w in warnings)  # CE-003, the operator note


def test_coaching_lint_flags_answer_stated_in_evidence(broken):
    def mutate(entries):
        case = next(e for e in entries if e.get("id") == "C01")
        case["evidence"][0]["content"] = "TT-101 rising — increase coolant flow immediately"

    broken.jsonl("procedures_cases.jsonl", mutate)
    _, warnings = validate_cartridge(broken.root)
    assert any("coaching" in w for w in warnings)


def test_procedure_applies_to_unknown_action(broken):
    broken.jsonl("procedures_cases.jsonl", lambda es: es[0].update(applies_to="paint_the_fence"))
    assert "applies_to unknown action" in broken.errors()


def test_load_raises_with_named_problems(broken):
    broken.manifest(lambda m: m.update(world="tokamak"))
    with pytest.raises(CartridgeError, match="unknown world"):
        load_cartridge(broken.root)


def test_sop_steps_become_checker_predicates(cstr):
    assert cstr.rulebook.get("increase_coolant_flow").required_steps == [
        "verify_coolant_flow", "check_valve_lineup",
    ]


def test_case_accessors(cstr):
    case = cstr.case("C01")
    assert case.decided_at() == 60.0  # explicit decision_time_s
    with pytest.raises(KeyError, match="no case"):
        cstr.case("C99")
    assert cstr.recovery_for("increase_coolant_flow").startswith("restore FC-201")
    assert cstr.recovery_for("verify_sensor") is None
    ctx = cstr.context_for(case)
    assert "adjust_process" in ctx.authority_scope and ctx.mode == "normal"
    assert "TT-101" in cstr.tags()
