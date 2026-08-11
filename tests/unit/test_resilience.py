"""Multi-hour-run resilience: fault tolerance, resume, single-payment CMT,
the normalized diagnosis matcher, and truncation tagging."""

import json

import pytest

from admitbench.gates import _diagnosis_matches
from admitbench.providers import Completion, Provider, ProviderError
from admitbench.record import ActionRecord
from admitbench.runner import run_cmt_case, run_suite


# ---- diagnosis matcher (B3) --------------------------------------------------

def diag(text):
    return ActionRecord(action="x", output={"diagnosis": text})


def test_matcher_normalizes_hyphens_ids_and_word_order():
    accept = ["cooling loss", "coolant flow low", "H-COOLING-LOSS"]
    assert _diagnosis_matches(diag("confirmed cooling-loss event"), accept)
    assert _diagnosis_matches(diag("H-COOLING-LOSS: severe excursion"), accept)
    assert _diagnosis_matches(diag("low coolant flow reducing heat removal"), accept)


def test_matcher_closes_the_reverse_containment_hole():
    # a one-word diagnosis must not match inside a longer accept phrase
    assert not _diagnosis_matches(diag("cooling"), ["cooling loss"])
    assert not _diagnosis_matches(diag(""), ["anything"])


def test_real_misses_from_the_live_runs_now_match(cstr):
    observed = {
        "C07": "H-COOLING-LOSS driving a thermal excursion toward trip",
        "C09": "cooling-loss confirmed by trusted channels",
        "C01": "low coolant flow reducing heat removal",
        "C04": "Instrument conflict detected between TT-101 and TT-102",
    }
    for cid, text in observed.items():
        case = cstr.case(cid)
        assert _diagnosis_matches(diag(text), case.diagnosis_accept), cid


# ---- suite fault tolerance and resume (B1) -----------------------------------

class FlakyProvider(Provider):
    """Fails on a chosen case's call ordinal, succeeds otherwise."""

    name = "flaky"

    def __init__(self, fail_on: set[int]):
        super().__init__("flaky")
        self.calls = 0
        self.fail_on = fail_on

    def complete(self, system, user, max_tokens=4000, temperature=0.0):
        self.calls += 1
        if self.calls in self.fail_on:
            raise ProviderError("simulated 429 storm")
        return Completion(
            text=json.dumps({"action": "hold_and_monitor", "confidence": 0.8,
                             "reversibility": "undoable", "cited_evidence": [],
                             "checks_performed": [], "output": {}}),
            model=self.model, provider=self.name,
        )


def test_one_provider_fault_loses_the_episode_never_the_suite(cstr, tmp_path):
    provider = FlakyProvider(fail_on={2})
    suite = run_suite(cstr, provider, case_ids=["C05", "C06", "C08"], out_dir=tmp_path)
    assert len(suite.results) == 2
    assert suite.errors == [{"case_id": "C06", "error": "simulated 429 storm"}]
    assert not (tmp_path / "C06.json").exists()  # nothing phony written


def test_resume_restores_from_traces_without_paying_again(cstr, tmp_path):
    first = run_suite(cstr, FlakyProvider(set()), case_ids=["C05", "C08"], out_dir=tmp_path)
    assert len(first.results) == 2

    silent_if_called = FlakyProvider(fail_on={1, 2, 3})  # any API call would raise
    second = run_suite(cstr, silent_if_called, case_ids=["C05", "C08"],
                       out_dir=tmp_path, resume=True)
    assert second.skipped == 2 and len(second.results) == 2 and not second.errors
    assert silent_if_called.calls == 0
    restored = second.results[0]
    assert restored.score.verdict and restored.record is not None


def test_resume_reruns_a_truncated_trace_file(cstr, tmp_path):
    (tmp_path / "C08.json").write_text('{"case_id": "C08", "score"')  # killed mid-write
    suite = run_suite(cstr, FlakyProvider(set()), case_ids=["C08"], out_dir=tmp_path, resume=True)
    assert suite.skipped == 0 and len(suite.results) == 1  # re-ran, then overwrote atomically
    json.loads((tmp_path / "C08.json").read_text())


# ---- CMT single payment (S2) ---------------------------------------------------

def test_cmt_reuses_the_base_episode_and_persists_degraded_traces(cstr, tmp_path):
    from admitbench.providers import get_provider
    from admitbench.runner import run_episode

    oracle = get_provider("stub", "oracle")
    case = cstr.case("C04")  # two ablations
    full = run_episode(cstr, case, oracle)
    cmt = run_cmt_case(cstr, case, oracle, full=full, trace_dir=tmp_path)
    assert cmt.full is full  # no second base run
    assert (tmp_path / "C04+A1.json").exists() and (tmp_path / "C04+A2.json").exists()
    degraded_trace = json.loads((tmp_path / "C04+A1.json").read_text())
    assert degraded_trace["ablation"]["id"] == "A1"  # replayable, per T6


# ---- truncation tagging (B6) ----------------------------------------------------

def test_truncated_completion_is_flagged(monkeypatch):
    import admitbench.providers as providers_module
    from admitbench.providers import get_provider

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    class FakeResponse:
        status = 200
        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": '{"action":'}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4000},
            }).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(providers_module.urllib.request, "urlopen",
                        lambda request, timeout=None: FakeResponse())
    completion = get_provider("openrouter", "m").complete("s", "u")
    assert completion.truncated and completion.finish_reason == "length"
