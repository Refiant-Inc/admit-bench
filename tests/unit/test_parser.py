from admitbench.parser import parse_action_record


def test_bare_json_object():
    record, error = parse_action_record('{"action": "hold_and_monitor", "confidence": 0.8}')
    assert error is None and record.action == "hold_and_monitor"


def test_fenced_json_with_prose_around_it():
    text = 'Here is my decision:\n```json\n{"action": "verify_sensor", "params": {"tag": "TT-101"}}\n```\nThanks!'
    record, error = parse_action_record(text)
    assert error is None and record.action == "verify_sensor" and record.params == {"tag": "TT-101"}


def test_unfenced_object_inside_prose():
    text = 'I will act now. {"action": "escalate_to_operator", "confidence": 0.7} That is safest.'
    record, error = parse_action_record(text)
    assert error is None and record.action == "escalate_to_operator"


def test_braces_inside_strings_do_not_break_balancing():
    text = '{"action": "hold_and_monitor", "rationale": "the {weird} log line \\" with a quote"}'
    record, error = parse_action_record(text)
    assert error is None and "{weird}" in record.rationale


def test_trailing_comma_is_repaired():
    record, error = parse_action_record('{"action": "hold_and_monitor", "confidence": 0.5,}')
    assert error is None and record.confidence == 0.5


def test_prose_only_is_a_t0_matter_not_a_guess():
    record, error = parse_action_record("I would investigate the cooling system first.")
    assert record is None and "no JSON" in error


def test_empty_output():
    record, error = parse_action_record("   \n ")
    assert record is None and "empty" in error


def test_json_without_action_field_rejected():
    record, error = parse_action_record('{"output": {"answer": 42}}')
    assert record is None and "action" in error


def test_raw_text_is_preserved_for_replay():
    text = 'noise {"action": "hold_and_monitor"} noise'
    record, _ = parse_action_record(text)
    assert record.raw == text
