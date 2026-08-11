import json

import pytest

from admitbench.builder import (
    AUTHORING_PROMPT,
    draft_cartridge,
    ingest_operator_log,
    promote,
)
from admitbench.cartridge import validate_cartridge

SAMPLE = """Reactor R-201 runs an exothermic reaction. TT-201 measures temperature;
trip at 380 K. FT-301 measures coolant flow. Loss of cooling leads to thermal
runaway within minutes. Fouling of the jacket causes reduced heat removal.
Response procedure:
1. Verify coolant flow at FT-301
2. Check valve lineup
3. Increase coolant flow if temperature keeps rising
"""


def test_heuristic_draft_extracts_and_compiles(tmp_path):
    out = draft_cartridge(SAMPLE, tmp_path / "draft", cartridge_id="r201", world="cstr")
    for name in ("manifest.yaml", "system_graph.jsonl", "safety_case_graph.jsonl",
                 "procedures_cases.jsonl", "BUILD_REPORT.md"):
        assert (out / name).exists(), name

    system = [json.loads(l) for l in (out / "system_graph.jsonl").read_text().splitlines() if l.strip()]
    tags = {e["id"] for e in system if e["kind"] == "tag"}
    assert {"TT-201", "FT-301"} <= tags  # instrument tags; assets need a human or an LLM pass

    safety = [json.loads(l) for l in (out / "safety_case_graph.jsonl").read_text().splitlines() if l.strip()]
    assert any(e["kind"] == "hazard" for e in safety)
    assert any(e["kind"] == "cause_effect" for e in safety)
    assert all(e.get("review_status") == "candidate" for e in safety)  # drafts never arrive validated

    errors, _ = validate_cartridge(out)
    assert not errors  # a draft compiles; it just has no cases yet
    assert "review" in (out / "BUILD_REPORT.md").read_text().lower()


def test_ingest_operator_log_appends_candidates(tmp_path):
    out = draft_cartridge(SAMPLE, tmp_path / "draft", cartridge_id="r201")
    added = ingest_operator_log(
        out,
        "Pump seal chatter causes brief FT-301 dips that self-clear. "
        "High feed temperature leads to faster runaway onset.",
        logged_by="night shift",
    )
    assert len(added) == 2
    assert all(e["review_status"] == "candidate" and e["source"] == "operator_log" for e in added)
    assert added[0]["id"] == "CE-OP-001"
    assert "causes" not in added[0]["cause"]  # split happened at the marker


def test_promotion_ladder(tmp_path):
    out = draft_cartridge(SAMPLE, tmp_path / "draft", cartridge_id="r201")
    added = ingest_operator_log(out, "Valve chatter causes flow dips.", logged_by="ops")
    entry_id = added[0]["id"]

    with pytest.raises(ValueError, match="not a legal transition"):
        promote(out, entry_id, "validated")  # candidate cannot jump the ladder
    assert promote(out, entry_id, "reviewed")["review_status"] == "reviewed"
    assert promote(out, entry_id, "validated")["review_status"] == "validated"
    with pytest.raises(ValueError, match="not a legal transition"):
        promote(out, entry_id, "reviewed")  # no demotion; deprecate instead
    assert promote(out, entry_id, "deprecated")["review_status"] == "deprecated"

    with pytest.raises(KeyError, match="no entry"):
        promote(out, "CE-MISSING", "reviewed")
    with pytest.raises(ValueError, match="unknown status"):
        promote(out, entry_id, "blessed")


def test_authoring_prompt_is_complete():
    for fragment in ("manifest.yaml", "system_graph.jsonl", "safety_case_graph.jsonl",
                     "procedures_cases.jsonl", "admitbench validate", "candidate"):
        assert fragment in AUTHORING_PROMPT, fragment
