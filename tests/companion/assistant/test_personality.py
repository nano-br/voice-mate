"""The personality sources of the assistant and the Obsidian vault suggestions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.companion.assistant import personality
from app.companion.contract import AssistantSettings


def test_custom_text_round_trips_with_unix_newlines(tmp_path: Path) -> None:
    path = tmp_path / "state" / "personality.md"
    assert personality.read_custom(path) == ""
    personality.save_custom("Calm.\r\nShort answers.", path)
    assert personality.read_custom(path) == "Calm.\nShort answers."
    assert b"\r" not in path.read_bytes()
    assert [p.name for p in path.parent.iterdir()] == ["personality.md"]


def test_custom_text_has_a_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        personality.save_custom("x" * (personality.CUSTOM_LIMIT + 1), tmp_path / "p.md")


def test_the_effective_file_follows_the_source(tmp_path: Path) -> None:
    custom = tmp_path / "personality.md"
    chosen = AssistantSettings(personality_source="file", personality_file="C:/vault/me.md")
    assert personality.effective_file(chosen, custom) == "C:/vault/me.md"
    none = AssistantSettings(personality_source="none", personality_file="C:/vault/me.md")
    assert personality.effective_file(none, custom) == ""
    written = AssistantSettings(personality_source="custom")
    assert personality.effective_file(written, custom) == ""  # nothing written yet
    personality.save_custom("Calm.", custom)
    assert personality.effective_file(written, custom) == str(custom)


def _obsidian(appdata: Path, vaults: object) -> None:
    folder = appdata / "obsidian"
    folder.mkdir(parents=True)
    (folder / "obsidian.json").write_text(json.dumps({"vaults": vaults}), encoding="utf-8")


def test_obsidian_vaults_are_the_existing_ones_newest_first(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _obsidian(
        tmp_path / "appdata",
        {
            "a": {"path": str(old), "ts": 100},
            "b": {"path": str(new), "ts": 200},
            "c": {"path": str(tmp_path / "gone"), "ts": 300},
            "d": {"nopath": True},
        },
    )
    assert personality.obsidian_vaults(str(tmp_path / "appdata")) == [new, old]


@pytest.mark.parametrize("content", [None, "not json", '{"vaults": []}'])
def test_obsidian_vaults_tolerate_a_missing_or_odd_file(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        (tmp_path / "obsidian").mkdir()
        (tmp_path / "obsidian" / "obsidian.json").write_text(content, encoding="utf-8")
    assert personality.obsidian_vaults(str(tmp_path)) == []
    assert personality.obsidian_vaults("") == []


def test_vault_problem(tmp_path: Path) -> None:
    assert personality.vault_problem("") == ""
    assert personality.vault_problem(str(tmp_path / "gone")) == "missing"
    assert personality.vault_problem(str(tmp_path)) == "no_obsidian"
    (tmp_path / ".obsidian").mkdir()
    assert personality.vault_problem(str(tmp_path)) == ""
