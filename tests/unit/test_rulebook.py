import pytest

from admitbench.rulebook import Rule, RuleBook


def test_rule_rejects_unknown_class_and_reversibility():
    with pytest.raises(ValueError, match="action class"):
        Rule(action="x", action_class="yolo", reversibility="undoable")
    with pytest.raises(ValueError, match="reversibility"):
        Rule(action="x", action_class="observe", reversibility="sometimes")


def test_from_entries_accepts_class_alias_and_rejects_duplicates():
    book = RuleBook.from_entries([{"action": "look", "class": "observe", "reversibility": "undoable"}])
    assert book.get("look").action_class == "observe"
    with pytest.raises(ValueError, match="duplicate"):
        RuleBook.from_entries(
            [
                {"action": "look", "class": "observe", "reversibility": "undoable"},
                {"action": "look", "class": "hold", "reversibility": "undoable"},
            ]
        )


def test_commits_state_and_boldness():
    commit = Rule(action="do", action_class="commit_costly", reversibility="costly_to_undo")
    watch = Rule(action="see", action_class="observe", reversibility="undoable")
    assert commit.commits_state() and not watch.commits_state()
    assert commit.boldness > watch.boldness


def test_attach_procedure_sets_required_steps_and_rejects_unknown_action():
    book = RuleBook.from_entries([{"action": "do", "class": "commit_reversible", "reversibility": "undoable"}])
    book.attach_procedure("do", ["a", "b"])
    assert book.get("do").required_steps == ["a", "b"]
    with pytest.raises(KeyError, match="unknown action"):
        book.attach_procedure("nope", ["a"])


def test_lookup_surface(cstr):
    book = cstr.rulebook
    assert "increase_coolant_flow" in book
    assert "made_up" not in book
    assert book.get("made_up") is None
    assert book.actions() == sorted(book.actions())
    assert len(book.rules()) == len(book.actions())
