"""The person's standing instructions: kept once, persisted, forgotten by text or fragment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_an_unreadable_file_is_set_aside_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "i.json"
    path.write_text("{not json", encoding="utf-8")
    store = StandingInstructions(path)
    assert store.items() == ()
    assert (tmp_path / "i.json.bad").read_text(encoding="utf-8") == "{not json"
    assert store.add("A new rule.")  # saving later does not lose the old file
    assert (tmp_path / "i.json.bad").read_text(encoding="utf-8") == "{not json"
    path.write_text("[1, 2]", encoding="utf-8")
    StandingInstructions(path)
    assert (tmp_path / "i.json.bad").read_text(encoding="utf-8") == "[1, 2]"


@pytest.mark.parametrize("since", [1e999, -1e999, 1e300, -5.0, 10.0, 9_999_999_999_999.0])
def test_a_since_out_of_range_reads_as_unknown(tmp_path: Path, since: float) -> None:
    path = tmp_path / "i.json"
    path.write_text(json.dumps({"instructions": [{"text": "ok", "since": since}]}), encoding="utf-8")
    store = StandingInstructions(path)
    assert [(item.text, item.since) for item in store.items()] == [("ok", 0.0)]
    assert store.as_json() == [{"text": "ok", "since": ""}]


def test_a_clock_out_of_range_still_writes_the_context(tmp_path: Path) -> None:
    store = StandingInstructions(tmp_path / "i.json", clock=lambda: 1e300)
    assert store.add("Keep it short.")
    assert store.as_json() == [{"text": "Keep it short.", "since": ""}]
