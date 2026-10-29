"""The assistant skill installer: fresh install, idempotence, edited files, removal and the
`assistant.json` keys the skill reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.companion.assistant import skill_install
from app.companion.assistant.skill_install import (
    PACKAGED_LANGUAGES,
    SKILL_NAME,
    AssistantContext,
    SkillSource,
    install_skill,
    packaged_language,
    packaged_skill_dir,
    remove_skill,
    settings_file_path,
    skill_source,
    write_context,
)
from app.companion.contract import SkillLanguage


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def _packaged_names() -> list[str]:
    return sorted(p.name for p in packaged_skill_dir().iterdir() if p.is_file())


def test_packaged_skill_has_the_two_files() -> None:
    assert _packaged_names() == ["SKILL.md", "config.example.json"]


def test_fresh_install_defaults_to_the_claude_home(home: Path) -> None:
    result = install_skill()

    skill_dir = home / ".claude" / "skills" / "voicemate-assistant"
    assert result.skill_dir == skill_dir
    assert result.changed
    assert result.kept_edited == ()
    for name in _packaged_names():
        assert (skill_dir / name).read_bytes() == (packaged_skill_dir() / name).read_bytes()
    sidecar = json.loads((skill_dir / ".voicemate-managed.json").read_text(encoding="utf-8"))
    assert sidecar["skill_version"] == skill_install.SKILL_VERSION
    assert sorted(sidecar["files"]) == _packaged_names()


def test_claude_config_dir_wins(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = home / "custom-claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))

    result = install_skill()

    assert result.skill_dir == custom / "skills" / "voicemate-assistant"
    assert (result.skill_dir / "SKILL.md").is_file()


def test_reinstall_is_a_no_op(tmp_path: Path) -> None:
    install_skill(tmp_path)

    result = install_skill(tmp_path)

    assert not result.changed
    assert result.written == ()
    assert result.kept_edited == ()


def test_edited_file_is_kept_and_the_new_version_goes_beside_it(tmp_path: Path) -> None:
    first = install_skill(tmp_path)
    edited = first.skill_dir / "SKILL.md"
    edited.write_text("my own version\n", encoding="utf-8")

    result = install_skill(tmp_path)

    assert result.changed
    assert result.kept_edited == ("SKILL.md",)
    assert edited.read_text(encoding="utf-8") == "my own version\n"
    assert (first.skill_dir / "SKILL.md.new").read_bytes() == (packaged_skill_dir() / "SKILL.md").read_bytes()
    assert not install_skill(tmp_path).changed


def test_unedited_older_version_is_updated(tmp_path: Path) -> None:
    first = install_skill(tmp_path)
    target = first.skill_dir / "config.example.json"
    sidecar_path = first.skill_dir / ".voicemate-managed.json"
    # An older VoiceMate wrote different content and recorded its hash.
    target.write_bytes(b"{}\n")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["files"]["config.example.json"] = skill_install._sha256(b"{}\n")
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    result = install_skill(tmp_path)

    assert result.written == ("config.example.json",)
    assert result.kept_edited == ()
    assert target.read_bytes() == (packaged_skill_dir() / "config.example.json").read_bytes()


def test_remove_deletes_only_managed_unedited_files(tmp_path: Path) -> None:
    skill_dir = install_skill(tmp_path).skill_dir
    (skill_dir / "SKILL.md").write_text("mine\n", encoding="utf-8")

    result = remove_skill(tmp_path)

    assert result.removed == ("config.example.json",)
    assert result.kept_edited == ("SKILL.md",)
    assert (skill_dir / "SKILL.md").exists()
    assert not (skill_dir / "config.example.json").exists()
    assert not (skill_dir / ".voicemate-managed.json").exists()


def test_remove_cleans_the_folder_when_nothing_was_edited(tmp_path: Path) -> None:
    skill_dir = install_skill(tmp_path).skill_dir

    remove_skill(tmp_path)

    assert not skill_dir.exists()


def test_context_json_has_exactly_the_keys_the_skill_reads(home: Path) -> None:
    ctx = AssistantContext(
        language="pt-BR",
        user_name="Alli",
        personality_file="C:/p.md",
        expressions="on",
        mode="desktop",
        settings=[{"key": "cues.volume", "type": "number", "range": [0, 100], "value": 70, "description": "x"}],
        inbox=[{"session_id": "s1", "title": "Build", "ready_at": "2026-10-28T10:00:00+00:00"}],
        pending=[{"session_id": "s2", "title": "Docs", "reason": "the review", "since": "2026-10-28T09:00:00+00:00"}],
        notes_vault="C:/vault",
        answer_delivery="sound",
    )

    path = write_context(ctx)

    assert path == settings_file_path() == home / ".voicemate" / "assistant.json"
    raw = path.read_bytes()
    assert b"\r" not in raw
    assert json.loads(raw.decode("utf-8")) == {
        "version": 1,
        "language": "pt-BR",
        "user_name": "Alli",
        "personality_file": "C:/p.md",
        "notes_vault": "C:/vault",
        "expressions": "on",
        "mode": "desktop",
        "answer_delivery": "sound",
        "inbox": [{"session_id": "s1", "title": "Build", "ready_at": "2026-10-28T10:00:00+00:00"}],
        "pending": [
            {"session_id": "s2", "title": "Docs", "reason": "the review", "since": "2026-10-28T09:00:00+00:00"}
        ],
        "settings": [{"key": "cues.volume", "type": "number", "range": [0, 100], "value": 70, "description": "x"}],
    }
    for language in PACKAGED_LANGUAGES:
        example = json.loads((packaged_skill_dir(language) / "config.example.json").read_text(encoding="utf-8"))
        assert set(json.loads(raw)) == set(example), language
    assert list(path.parent.iterdir()) == [path]  # no temp file left behind


@pytest.mark.parametrize("language", PACKAGED_LANGUAGES)
def test_the_packaged_skill_matches_the_lab_copy(language: str) -> None:
    packaged = packaged_skill_dir(language)  # type: ignore[arg-type]
    lab = Path(__file__).resolve().parents[2] / "tools" / "lab" / "assistant_skill" / language / SKILL_NAME
    names = sorted(p.name for p in packaged.iterdir() if p.is_file())
    assert names == sorted(p.name for p in lab.iterdir() if p.is_file())
    for name in names:
        assert (packaged / name).read_bytes() == (lab / name).read_bytes(), f"{language}/{name}"


@pytest.mark.parametrize(
    ("skill_language", "interface", "expected"),
    [
        ("auto", "pt-BR", "pt-BR"),
        ("auto", "en-US", "en"),
        ("auto", "es-ES", "en"),
        ("en", "pt-BR", "en"),
        ("pt-BR", "en-US", "pt-BR"),
    ],
)
def test_the_skill_language_follows_the_setting_or_the_interface(
    skill_language: SkillLanguage, interface: str, expected: str
) -> None:
    assert packaged_language(skill_language, interface) == expected
    source = skill_source(skill_language, "", interface)
    assert source == SkillSource(packaged_skill_dir(expected), expected)  # type: ignore[arg-type]


def test_a_custom_folder_needs_a_skill_md(tmp_path: Path) -> None:
    custom = tmp_path / "mine"
    custom.mkdir()
    rejected = skill_source("auto", str(custom), "en-US")
    assert rejected == SkillSource(packaged_skill_dir("en"), "en", rejected_custom=str(custom))
    (custom / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    assert skill_source("auto", str(custom), "en-US") == SkillSource(custom, None)


def test_switching_the_skill_reinstalls_and_drops_what_the_new_one_lacks(tmp_path: Path) -> None:
    claude_home = tmp_path / "claude"
    english = install_skill(claude_home, packaged_skill_dir("en"))
    skill_md = english.skill_dir / "SKILL.md"
    assert skill_md.read_bytes() == (packaged_skill_dir("en") / "SKILL.md").read_bytes()

    portuguese = install_skill(claude_home, packaged_skill_dir("pt-BR"))
    assert portuguese.changed
    assert skill_md.read_bytes() == (packaged_skill_dir("pt-BR") / "SKILL.md").read_bytes()

    custom = tmp_path / "custom"
    custom.mkdir()
    (custom / "SKILL.md").write_text("# mine\n", encoding="utf-8")
    install_skill(claude_home, custom)
    assert skill_md.read_text(encoding="utf-8") == "# mine\n"
    assert not (english.skill_dir / "config.example.json").exists()  # not in the custom folder
