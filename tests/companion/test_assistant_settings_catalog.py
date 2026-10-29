"""The settings the assistant may read and change by voice."""

from __future__ import annotations

from dataclasses import replace

import pytest
from companion_core_fakes import use_english

from app.companion.assistant import settings_catalog
from app.companion.contract import AssistantSettings, CompanionSettings


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    use_english(monkeypatch)


def _keys(settings: CompanionSettings) -> list[str]:
    return [str(entry["key"]) for entry in settings_catalog.catalog(settings)]


def test_the_wave_3_entries_are_in_the_catalog() -> None:
    keys = _keys(CompanionSettings())
    for key in (
        "assistant.new_conversation_model",
        "assistant.new_conversation_effort",
        "assistant.personality_source",
        "assistant.desktop_profile_auto_update",
        "assistant.answer_delivery",
        "assistant.permission_mode",
    ):
        assert key in keys, key


def test_the_voice_can_be_turned_off_and_chosen_by_voice() -> None:
    assert {"assistant.speak_replies", "assistant.voice"} <= set(_keys(CompanionSettings()))
    quiet = settings_catalog.change(CompanionSettings(), "assistant.speak_replies", False)
    assert isinstance(quiet, CompanionSettings)
    assert quiet.assistant.speak_replies is False
    chosen = settings_catalog.change(CompanionSettings(), "assistant.voice", "narrator")
    assert isinstance(chosen, CompanionSettings)
    assert chosen.assistant.voice == "narrator"
    refused = settings_catalog.change(CompanionSettings(), "assistant.voice", "two\nlines")
    assert isinstance(refused, settings_catalog.Refusal)


def test_paths_are_never_in_the_catalog() -> None:
    keys = _keys(CompanionSettings())
    for hidden in ("personality_file", "obsidian_vault", "desktop_profile_override", "custom_skill_dir"):
        assert not any(key.endswith(hidden) for key in keys), hidden


def test_entries_carry_the_current_value_and_the_choices() -> None:
    settings = CompanionSettings(assistant=AssistantSettings(new_conversation_model="opus"))
    entry = next(e for e in settings_catalog.catalog(settings) if e["key"] == "assistant.new_conversation_model")
    assert entry["value"] == "opus"
    assert entry["values"] == ["haiku", "sonnet", "opus"]
    assert entry["type"] == "choice"


def test_change_sets_the_new_conversation_defaults() -> None:
    changed = settings_catalog.change(CompanionSettings(), "assistant.new_conversation_effort", "high")
    assert isinstance(changed, CompanionSettings)
    assert changed.assistant.new_conversation_effort == "high"
    switched = settings_catalog.change(CompanionSettings(), "assistant.desktop_profile_auto_update", False)
    assert isinstance(switched, CompanionSettings)
    assert switched.assistant.desktop_profile_auto_update is False


def test_the_file_personality_needs_a_chosen_file() -> None:
    refused = settings_catalog.change(CompanionSettings(), "assistant.personality_source", "file")
    assert refused == settings_catalog.Refusal("no_personality_file")
    assert settings_catalog.refusal_reason(refused) == "choose the personality file first, in Settings, Assistant"
    assert "no personality file" in settings_catalog.refusal_reason_en(refused)
    with_file = CompanionSettings(assistant=AssistantSettings(personality_file="C:/me.md"))
    changed = settings_catalog.change(with_file, "assistant.personality_source", "file")
    assert isinstance(changed, CompanionSettings)
    assert changed.assistant.personality_source == "file"
    custom = settings_catalog.change(CompanionSettings(), "assistant.personality_source", "custom")
    assert isinstance(custom, CompanionSettings)


def test_desktop_mode_needs_a_conversation() -> None:
    refused = settings_catalog.change(CompanionSettings(), "assistant.mode", "desktop")
    assert refused == settings_catalog.Refusal("no_desktop_conversation")
    chosen = replace(CompanionSettings(), assistant=AssistantSettings(desktop_conversation="VoiceMate"))
    changed = settings_catalog.change(chosen, "assistant.mode", "desktop")
    assert isinstance(changed, CompanionSettings)
    assert changed.assistant.mode == "desktop"


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("assistant.nope", "x", "unknown_key"),
        ("assistant.answer_delivery", "shout", "not_allowed"),
        ("assistant.desktop_profile_auto_update", "yes", "not_bool"),
        ("assistant.user_name", "two\nlines", "bad_text"),
    ],
)
def test_bad_values_are_refused(key: str, value: object, code: str) -> None:
    refused = settings_catalog.change(CompanionSettings(), key, value)
    assert isinstance(refused, settings_catalog.Refusal)
    assert refused.code == code
    assert settings_catalog.refusal_reason(refused)
    assert settings_catalog.refusal_reason_en(refused)


def test_display_values() -> None:
    assert settings_catalog.display_value("assistant.desktop_profile_auto_update", True) == "on"
    assert settings_catalog.display_value("assistant.new_conversation_model", "opus") == "Opus (deepest)"
    assert (
        settings_catalog.display_value("assistant.answer_delivery", "sound") == "Only play a sound and keep the answer"
    )
    # A value the settings window has no name for is shown as it is.
    assert settings_catalog.display_value("assistant.answer_delivery", "later") == "later"


def test_full_access_is_never_granted_by_voice() -> None:
    refused = settings_catalog.change(CompanionSettings(), "assistant.permission_mode", "bypassPermissions")
    assert refused == settings_catalog.Refusal("not_allowed", "default, acceptEdits")
    entry = next(e for e in settings_catalog.catalog(CompanionSettings()) if e["key"] == "assistant.permission_mode")
    assert entry["values"] == ["default", "acceptEdits"]
    lowered = settings_catalog.change(
        CompanionSettings(assistant=AssistantSettings(permission_mode="bypassPermissions")),
        "assistant.permission_mode",
        "default",
    )
    assert isinstance(lowered, CompanionSettings)
    assert lowered.assistant.permission_mode == "default"
