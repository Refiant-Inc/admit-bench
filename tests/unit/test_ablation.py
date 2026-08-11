import pytest

from admitbench.ablation import AblationSpec, apply_ablation


def test_remove_deletes_entry_and_leaves_original_untouched(cstr, c01):
    _, log, _ = c01
    degraded = apply_ablation(log, AblationSpec(id="a", op="remove", evidence_ids=["ev_ft201"]))
    assert degraded.get("ev_ft201") is None
    assert log.get("ev_ft201") is not None
    assert degraded.verify_chain()  # a degraded ledger is a new attested ledger


def test_stale_and_quarantine_set_quality(c01):
    _, log, _ = c01
    stale = apply_ablation(log, AblationSpec(id="a", op="stale", evidence_ids=["ev_ft201"]))
    assert stale.get("ev_ft201").quality == "stale"
    quarantined = apply_ablation(log, AblationSpec(id="a", op="quarantine", evidence_ids=["ev_tt101"]))
    assert quarantined.get("ev_tt101").quality == "quarantined"
    assert stale.verify_chain() and quarantined.verify_chain()


def test_untrust_flips_channel_and_existing_entries(c01):
    _, log, _ = c01
    degraded = apply_ablation(log, AblationSpec(id="a", op="untrust", source="historian"))
    assert degraded.trust_map["historian"] == "untrusted"
    assert all(e.trust == "untrusted" for e in degraded.entries() if e.source == "historian")
    assert log.trust_map["historian"] == "trusted"  # original untouched


def test_unknown_op_and_missing_ids_raise(c01):
    _, log, _ = c01
    with pytest.raises(ValueError, match="unknown ablation op"):
        apply_ablation(log, AblationSpec(id="a", op="vaporize"))
    with pytest.raises(KeyError, match="no evidence"):
        apply_ablation(log, AblationSpec(id="a", op="stale", evidence_ids=["ev_ghost"]))
    with pytest.raises(ValueError, match="requires a source"):
        apply_ablation(log, AblationSpec(id="a", op="untrust"))


def test_from_dict_ignores_extra_keys():
    spec = AblationSpec.from_dict({"id": "a", "op": "remove", "evidence_ids": ["x"], "purpose": "cmt", "extra": 1})
    assert spec.id == "a" and spec.evidence_ids == ["x"]
    assert spec.to_dict()["op"] == "remove"
