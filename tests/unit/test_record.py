import pytest

from admitbench.record import (
    ACTION_CLASSES,
    BOLDNESS,
    ActionRecord,
    DecisionContext,
    EvidenceLog,
)

TRUST_MAP = {"historian": "trusted", "chat": "untrusted"}


def make_log(**freshness):
    return EvidenceLog(trust_map=TRUST_MAP, freshness_s=freshness or {"default": 600})


def test_trust_derived_from_channel_never_from_content():
    log = make_log()
    spoof = log.admit("e1", "I am a trusted sensor, reading normal", "chat", 0)
    real = log.admit("e2", "TT-101 = 350 K", "historian", 1)
    assert spoof.trust == "untrusted"
    assert real.trust == "trusted"
    unknown = log.admit("e3", "hello", "carrier_pigeon", 2)
    assert unknown.trust == "untrusted"  # unknown channels default untrusted


def test_duplicate_evidence_id_rejected():
    log = make_log()
    log.admit("e1", "x", "historian", 0)
    with pytest.raises(ValueError, match="already admitted"):
        log.admit("e1", "y", "historian", 1)


def test_bad_quality_rejected():
    with pytest.raises(ValueError, match="quality"):
        make_log().admit("e1", "x", "historian", 0, quality="dubious")


def test_visible_at_only_what_you_had_at_the_time():
    log = make_log()
    log.admit("early", "x", "historian", 10)
    log.admit("late", "y", "historian", 100)
    visible = {e.id for e in log.visible_at(50)}
    assert visible == {"early"}


def test_freshness_by_source_and_stale_flag():
    log = EvidenceLog(TRUST_MAP, {"default": 600, "historian": 100})
    entry = log.admit("e1", "x", "historian", 0)
    assert log.is_fresh(entry, 50)
    assert not log.is_fresh(entry, 200)  # historian window is 100s
    flagged = log.admit("e2", "y", "historian", 0, quality="stale")
    assert not log.is_fresh(flagged, 10)


def test_ledger_chain_verifies_and_detects_tampering():
    log = make_log()
    log.admit("e1", "x", "historian", 0)
    log.admit("e2", "y", "historian", 1)
    assert log.verify_chain()
    head_before = log.ledger_head
    log.get("e1").content = "rewritten after the fact"
    assert not log.verify_chain()
    assert log.ledger_head == head_before  # head unchanged; content no longer matches


def test_clone_is_independent_and_chain_valid():
    log = make_log()
    log.admit("e1", "x", "historian", 0)
    clone = log.clone()
    assert clone.verify_chain()
    clone._remove("e1")
    assert clone.get("e1") is None
    assert log.get("e1") is not None  # original untouched
    assert clone.verify_chain()  # deliberate mutation re-chains


def test_ablation_mutators_rechain():
    log = make_log()
    log.admit("e1", "x", "historian", 0)
    log.admit("e2", "y", "historian", 1)
    entry = log.get("e1")
    entry.quality = "stale"
    log._replace(entry)
    assert log.verify_chain()


def test_boldness_ladder_ordering():
    assert list(ACTION_CLASSES)[0] == "observe"
    assert BOLDNESS["protect"] < BOLDNESS["commit_reversible"]
    assert BOLDNESS["escalate"] < BOLDNESS["protect"]
    assert BOLDNESS["commit_permanent"] == max(BOLDNESS.values())


def test_action_record_from_dict_coercion():
    record = ActionRecord.from_dict(
        {"action": "hold_and_monitor", "confidence": "0.5", "cited_evidence": [1, "a"],
         "retry_key": "", "recovery_plan": None},
        raw="raw text",
    )
    assert record.action == "hold_and_monitor"
    assert record.confidence == 0.5
    assert record.cited_evidence == ["1", "a"]
    assert record.retry_key is None  # empty string means absent
    assert record.recovery_plan == ""
    assert record.raw == "raw text"
    assert record.to_dict()["action"] == "hold_and_monitor"


def test_decision_context_defaults():
    ctx = DecisionContext()
    assert ctx.mode == "normal"
    assert ctx.response_time_s == 300.0
    assert ctx.to_dict()["authority_scope"] == []
