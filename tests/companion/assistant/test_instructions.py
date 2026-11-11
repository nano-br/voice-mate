"""The person's standing instructions: kept once, persisted, forgotten by text or fragment."""

from __future__ import annotations

import json
from pathlib import Path

from app.companion.assistant.instructions import LIMIT, TEXT_LIMIT, StandingInstructions


def test_add_keeps_one_line_once_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "instructions.json"
    store = StandingInstructions(path, clock=lambda: 1_800_000_000.0)
    assert store.add("  Read other conversations\nwithout asking.  ")
    assert not store.add("read other conversations WITHOUT asking.")  # the same rule
    assert not store.add("   ")
    assert not store.add(42)
    assert [item.text for item in store.items()] == ["Read other conversations without asking."]
    assert store.as_json() == [
        {"text": "Read other conversations without asking.", "since": "2027-01-15T08:00:00+00:00"}
    ]
    again = StandingInstructions(path)
    assert [item.text for item in again.items()] == ["Read other conversations without asking."]
    assert b"\r" not in path.read_bytes()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["instructions.json"]  # no temp file left


def test_long_rules_are_cut_and_the_oldest_go_first(tmp_path: Path) -> None:
    store = StandingInstructions(tmp_path / "i.json")
    assert store.add("x" * (TEXT_LIMIT + 50))
    assert len(store.items()[0].text) == TEXT_LIMIT
    for number in range(LIMIT + 5):
        store.add(f"rule {number}")
    items = store.items()
    assert len(items) == LIMIT
    assert items[-1].text == f"rule {LIMIT + 4}"


def test_forget_by_exact_text_or_a_unique_fragment(tmp_path: Path) -> None:
    store = StandingInstructions(tmp_path / "i.json")
    store.add("Answer in one sentence.")
    store.add("Never ask before reading a conversation.")
    store.add("Never ask before asking a conversation for its status.")
    assert store.forget("nothing like it") == "missing"
    assert store.forget("never ask") == "ambiguous"
    assert store.forget(None) == "missing"
    assert store.forget("answer in one sentence.") == "removed"
    assert store.forget("for its status") == "removed"
    assert [item.text for item in store.items()] == ["Never ask before reading a conversation."]


def test_a_bad_file_is_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "i.json"
    path.write_text("{not json", encoding="utf-8")
    assert StandingInstructions(path).items() == ()
    path.write_text(json.dumps({"instructions": [1, {"text": ""}, {"text": "ok", "since": True}]}), encoding="utf-8")
    items = StandingInstructions(path).items()
    assert [(item.text, item.since) for item in items] == [("ok", 0.0)]
