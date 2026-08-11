"""The seal: the answer key never reaches an LLM call.

Two boundaries are deliberate and different. The safety knowledge (grammar,
SOPs, hazards, cause→effect graph) is open-book — an operator has the binder,
so does the agent. The per-case answer key (`oracle`, `acceptable_actions`,
`load_bearing`, `diagnosis_accept`, `escalation_ok`, `time_to_hazard_s`,
`notes`, ablation plans) is sealed: it exists only for the gates and the
scorer, and nothing that leaves the machine may depend on it.

The strongest test here is mutation invariance: rewrite the entire answer key
and the rendered prompt must not change by one byte.
"""

import copy

from admitbench.prompts import SEALED_MARKERS, render_system, render_user


def full_prompt(cartridge, case):
    log = cartridge.evidence_log_for(case)
    ctx = cartridge.context_for(case)
    return render_system(cartridge) + "\n" + render_user(cartridge, case, log, ctx)


def test_no_sealed_marker_ever_renders(cstr, column):
    for cartridge in (cstr, column):
        for case in cartridge.cases:
            prompt = full_prompt(cartridge, case).lower()
            for marker in SEALED_MARKERS:
                assert marker not in prompt, f"{cartridge.id}/{case.id} leaks {marker!r}"


def test_case_notes_never_render(cstr, column):
    for cartridge in (cstr, column):
        for case in cartridge.cases:
            if case.notes:
                assert case.notes[:40].lower() not in full_prompt(cartridge, case).lower(), (
                    f"{cartridge.id}/{case.id} leaks its notes"
                )


def test_prompt_is_invariant_under_answer_key_mutation(cstr, column):
    """Rewrite everything the scorer knows; the agent must see the same bytes."""
    for cartridge in (cstr, column):
        for case in cartridge.cases:
            before = full_prompt(cartridge, case)
            mutated = copy.deepcopy(case)
            mutated.oracle = {"action": "controlled_shutdown", "params": {"canary": 1}}
            mutated.acceptable_actions = ["canary_action"]
            mutated.load_bearing = ["ev_canary"]
            mutated.diagnosis_accept = ["canary diagnosis"]
            mutated.escalation_ok = not case.escalation_ok
            mutated.time_to_hazard_s = 1.0
            mutated.notes = "CANARY-NOTES-MUST-NOT-RENDER"
            mutated.ablations = [{"id": "AX", "op": "remove", "evidence_ids": []}]
            after = full_prompt(cartridge, mutated)
            assert before == after, f"{cartridge.id}/{case.id}: prompt depends on the answer key"


def test_stub_agents_are_the_only_reader_of_the_key(cstr, oracle):
    """The stub oracle reads case.oracle by design — it is a reference agent,
    not a model call. Real providers receive only the two prompt strings; this
    pins the runner's provider interface to exactly that surface."""
    import inspect

    from admitbench.providers import AnthropicProvider, OpenAICompatProvider

    for cls in (OpenAICompatProvider, AnthropicProvider):
        params = list(inspect.signature(cls.complete).parameters)
        assert params[:3] == ["self", "system", "user"], cls.__name__
