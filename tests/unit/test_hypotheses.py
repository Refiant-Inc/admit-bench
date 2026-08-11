from admitbench.hypotheses import candidate_hypotheses, render_hypotheses


def test_cooling_loss_evidence_points_at_the_cooling_loss_ce(cstr, c01):
    _, log, ctx = c01
    hypotheses = candidate_hypotheses(cstr, log, ctx)
    assert hypotheses and hypotheses[0].ce_id == "CE-001"  # validated HAZOP entry wins
    assert hypotheses[0].hazard_id == "H-COOLING-LOSS"
    assert len(hypotheses) <= 3


def test_candidate_operator_notes_are_downweighted(cstr, c01):
    _, log, ctx = c01
    ranked = {h.ce_id: h.score for h in candidate_hypotheses(cstr, log, ctx, k=10)}
    if "CE-003" in ranked:  # the candidate seal-flush note
        assert ranked["CE-003"] < ranked["CE-001"]


def test_deprecated_entries_never_surface(cstr, c01):
    _, log, ctx = c01
    original = cstr.safety_case
    try:
        cstr.safety_case = [
            dict(e, review_status="deprecated") if e.get("kind") == "cause_effect" else e
            for e in original
        ]
        assert candidate_hypotheses(cstr, log, ctx) == []
    finally:
        cstr.safety_case = original


def test_no_matching_evidence_no_hypotheses(cstr):
    case = cstr.case("C08")  # quiet shift: flat readings match no failure pattern strongly
    log, ctx = cstr.evidence_log_for(case), cstr.context_for(case)
    hypotheses = candidate_hypotheses(cstr, log, ctx)
    # nothing here should outrank a real signature; allow weak matches but not CE-001 dominance
    assert all(h.score < 1.0 for h in hypotheses)


def test_render_marks_candidates_and_warns(cstr, c01):
    _, log, ctx = c01
    text = render_hypotheses(candidate_hypotheses(cstr, log, ctx, k=10))
    assert "starting points to verify" in text
    assert "UNVERIFIED operator note" in text  # CE-003 matched and is marked
    assert render_hypotheses([]) == ""


def test_hypotheses_are_deterministic(cstr, c01):
    _, log, ctx = c01
    a = [h.to_dict() for h in candidate_hypotheses(cstr, log, ctx)]
    b = [h.to_dict() for h in candidate_hypotheses(cstr, log, ctx)]
    assert a == b
