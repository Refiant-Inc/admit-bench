"""The Layer-1 model runner: sealed prompts, honest scoring, resumability."""

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
L1 = REPO / "benchmarks" / "layer1_action_selection"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, L1 / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


runner = _load("run_layer1")
gen = _load("generate_layer1_scenarios")


class FakeCompletion:
    def __init__(self, text):
        self.text = text
        self.latency_s, self.input_tokens, self.output_tokens = 0.4, 120, 15
        self.cost_usd, self.finish_reason, self.truncated = 0.0007, "stop", False


class FakeProvider:
    """Echoes back a chosen letter; records the prompt it was handed so the
    test can prove the answer key never reached it."""
    name, model = "fake", "echo"

    def __init__(self, letter="A"):
        self.letter = letter
        self.seen = []

    def complete(self, system, user, max_tokens=600):
        self.seen.append(system + user)
        return FakeCompletion(f'{{"selected_action": "{self.letter}", "confidence": 0.7}}')


@pytest.fixture
def scenarios():
    return gen.generate(1, seed=42)[:6]


def test_run_scenario_scores_the_pick(scenarios):
    scenario = scenarios[0]
    trace = runner.run_scenario(scenario, FakeProvider(letter="A"))
    assert trace["selected_action"] == "A"
    assert trace["matrix_class"] in {"correct_safe", "incorrect_safe", "correct_unsafe", "incorrect_unsafe"}
    assert trace["sealed"] is True and trace["scenario_id"] == scenario["scenario_id"]


def test_prompt_handed_to_the_model_never_contains_the_key(scenarios):
    provider = FakeProvider()
    for scenario in scenarios:
        runner.run_scenario(scenario, provider)
    for prompt in provider.seen:
        for field in ("action_labels", "preferred_actions", "unsafe_actions", "correct_safe"):
            assert field not in prompt


def test_scoring_matches_the_sealed_key(scenarios):
    """Whichever letter is the correct_safe answer must score 1.0; a known
    unsafe letter must be a hard violation."""
    scenario = scenarios[0]
    safe_letter = scenario["preferred_actions"][0]
    unsafe_letter = scenario["unsafe_actions"][0]
    assert runner.run_scenario(scenario, FakeProvider(safe_letter))["score"] == 1.0
    unsafe = runner.run_scenario(scenario, FakeProvider(unsafe_letter))
    assert unsafe["score"] == 0.0 and unsafe["hard_violation"]


def test_suite_summary_and_resume(tmp_path, scenarios):
    provider = FakeProvider(letter="A")
    out = tmp_path / "run"
    first = runner.run_suite(scenarios, provider, out, resume=True)
    assert first["n"] == len(scenarios)
    assert set(first["matrix"]) <= {"correct_safe", "incorrect_safe", "correct_unsafe",
                                    "incorrect_unsafe", "unsupported_action"}
    assert (out / "_summary.json").exists()
    calls = len(provider.seen)

    # a second resumed pass re-pays for nothing
    runner.run_suite(scenarios, provider, out, resume=True)
    assert len(provider.seen) == calls


def test_a_sealing_breach_raises(scenarios, monkeypatch):
    """If the prompt builder ever leaked a key field, the runner must refuse
    rather than score a compromised episode."""
    monkeypatch.setattr(runner.prompts, "build_user_prompt",
                        lambda s: "unsafe_actions: " + str(s["unsafe_actions"]))
    with pytest.raises(RuntimeError, match="sealing breach"):
        runner.run_scenario(scenarios[0], FakeProvider())
