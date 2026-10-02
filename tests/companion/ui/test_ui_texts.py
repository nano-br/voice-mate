from __future__ import annotations

from typing import get_args

import pytest

pytest.importorskip("PySide6")

from app.companion.contract import (  # noqa: E402
    CompanionSettings,
    CompanionSnapshot,
    CueName,
    CuePreset,
    CueSource,
    EngineMode,
    FlowEntry,
    HotkeyBinding,
    HotkeyCheck,
    NotifyLevel,
    SupervisorState,
    TrayState,
    UiLanguage,
    WslRestartPolicy,
)
from app.companion.ui import texts  # noqa: E402
from app.companion.ui.view_model import chord_for, displayed_flows, idle_chord  # noqa: E402
from app.protocol.models import AudioHealth  # noqa: E402


def test_every_literal_option_has_a_label() -> None:
    assert set(texts.supervisor_labels()) == set(get_args(SupervisorState))
    assert set(texts.audio_labels()) == set(get_args(AudioHealth))
    assert set(texts.cue_labels()) == set(get_args(CueName))
    assert set(texts.cue_source_labels()) == set(get_args(CueSource))
    assert set(texts.cue_preset_labels()) == set(get_args(CuePreset))
    assert set(texts.language_labels()) == set(get_args(UiLanguage))
    assert set(texts.engine_mode_labels()) == set(get_args(EngineMode))
    assert set(texts.wsl_restart_labels()) == set(get_args(WslRestartPolicy))
    assert set(texts.notify_level_labels()) == set(get_args(NotifyLevel))
    assert set(texts.hotkey_check_labels()) == set(get_args(HotkeyCheck))


def test_language_names_are_written_in_their_own_language() -> None:
    """Never translated: anyone must find their language in a UI they cannot read."""
    labels = texts.language_labels()
    assert {code: name for code, name in labels.items() if code != "auto"} == {
        "pt-BR": "Português (Brasil)",
        "en": "English",
        "es": "Español",
        "ru": "Русский",
        "zh-CN": "中文（简体）",
    }


@pytest.mark.parametrize("state", get_args(TrayState))
def test_every_state_has_a_status_line(state: TrayState) -> None:
    assert texts.status_line(CompanionSnapshot(tray_state=state), "ctrl+alt+v", now=0.0)


def test_elapsed_time_format() -> None:
    assert texts.format_elapsed(0) == "00:00"
    assert texts.format_elapsed(72.9) == "01:12"
    assert texts.format_elapsed(3725) == "1:02:05"
    snapshot = CompanionSnapshot(tray_state="recording", recording_since=100.0)
    assert texts.status_line(snapshot, now=165.0) == "Recording 01:05"
    assert texts.status_line(CompanionSnapshot(tray_state="recording")) == "Recording"


def test_tooltip_is_short_enough_for_windows() -> None:
    snapshot = CompanionSnapshot(tray_state="error", detail="x" * 300)
    tip = texts.tooltip(snapshot)
    assert len(tip) <= texts.TOOLTIP_LIMIT
    assert tip.endswith("...")
    assert texts.tooltip(CompanionSnapshot(tray_state="idle", hotkeys_suspended=True)) == "VoiceMate\nShortcuts paused"


def test_shorten_keeps_one_line() -> None:
    assert texts.shorten("a\nb   c") == "a b c"
    assert texts.shorten("abcdefghij", 8) == "abcde..."


def test_flow_titles_disambiguate_flows_of_the_same_kind() -> None:
    flows = (
        FlowEntry("clipboard", "clipboard", "ctrl+alt+v"),
        FlowEntry("notes", "clipboard", "ctrl+alt+n"),
        FlowEntry("claude_chat", "claude_chat", "ctrl+alt+a"),
    )
    assert [texts.flow_title(flow, flows) for flow in flows] == ["Dictate (clipboard)", "Dictate (notes)", "Ask Claude"]


def test_flows_and_chords_before_and_after_the_engine_reports_them() -> None:
    settings = CompanionSettings(hotkeys=(HotkeyBinding("clipboard", "f9"), HotkeyBinding("claude_chat", "")))
    before = CompanionSnapshot()
    flows = displayed_flows(before, settings)
    assert [(flow.name, flow.kind) for flow in flows] == [("clipboard", "clipboard"), ("claude_chat", "claude_chat")]
    assert idle_chord(before, settings) == "f9"
    engine_flows = (FlowEntry("claude_chat", "claude_chat", "ctrl+alt+a"),)
    owned = CompanionSnapshot(flows=engine_flows, hotkeys_owned_by_engine=True)
    assert displayed_flows(owned, settings) == engine_flows
    assert chord_for(engine_flows[0], owned, settings) == "ctrl+alt+a"
    assert idle_chord(owned, settings) == "ctrl+alt+a"
