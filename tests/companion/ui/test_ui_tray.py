from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QAction, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon  # noqa: E402

from app.companion.contract import TrayState  # noqa: E402
from app.companion.ui import icons  # noqa: E402
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402
from app.companion.ui.tray import TrayIcon  # noqa: E402

ALL_STATES: tuple[TrayState, ...] = get_args(TrayState)


def _tray(ui: CompanionUi) -> TrayIcon:
    assert ui.tray is not None
    return ui.tray


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def _action(menu: QMenu, text: str) -> QAction:
    for action in menu.actions():
        if action.text().split("\t")[0] == text:
            return action
    raise AssertionError(f"no menu item {text!r} in {_texts(menu)}")


def _signature(state: TrayState, tone: icons.GlyphTone, size: int) -> bytes:
    """Gray levels + alpha: what is left of the icon for someone who cannot tell colors apart."""
    image = icons.tray_pixmap(state, tone, size).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    gray = bytearray()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            gray += bytes((color.lightness() // 32, color.alpha() // 32))
    return bytes(gray)


@pytest.mark.parametrize("size", [16, 32])
@pytest.mark.parametrize("tone", ["light", "dark"])
def test_every_tray_state_is_distinguishable_without_color(
    qapp: QApplication, tone: icons.GlyphTone, size: int
) -> None:
    signatures = {state: _signature(state, tone, size) for state in ALL_STATES}
    for first, second in itertools.combinations(ALL_STATES, 2):
        assert signatures[first] != signatures[second], f"{first} and {second} look the same at {size} px"


def test_tray_icon_has_every_size_and_follows_the_taskbar_tone(qapp: QApplication) -> None:
    icon = icons.tray_icon("recording", "light")
    available = {size.width() for size in icon.availableSizes()}
    assert set(icons.TRAY_SIZES) <= available
    light = icons.tray_pixmap("idle", "light", 16).toImage()
    dark = icons.tray_pixmap("idle", "dark", 16).toImage()
    assert light != dark


def test_app_icon_prefers_the_packaged_icon(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(icons, "ASSET_ICON", tmp_path / "missing.ico")
    assert not icons.app_icon().isNull()  # drawn fallback
    packaged = tmp_path / "voicemate.ico"
    icons.app_pixmap(48).save(str(packaged), "PNG")  # format detected by content
    monkeypatch.setattr(icons, "ASSET_ICON", packaged)
    assert icons.app_icon().availableSizes() == [icons.app_pixmap(48).size()]


def test_taskbar_tone_follows_the_windows_registry(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(icons.sys, "platform", "win32")
    monkeypatch.setattr(icons, "_windows_taskbar_is_light", lambda: True)
    assert icons.taskbar_glyph_tone() == "dark"
    monkeypatch.setattr(icons, "_windows_taskbar_is_light", lambda: False)
    assert icons.taskbar_glyph_tone() == "light"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"tray_state": "stopped"}, "VoiceMate is stopped"),
        ({"tray_state": "starting"}, "Starting engine..."),
        ({"tray_state": "restarting", "restarts": 2}, "Restarting (attempt 2)"),
        ({"tray_state": "idle"}, "Listening for Ctrl+Alt+V"),
        ({"tray_state": "transcribing"}, "Transcribing..."),
        ({"tray_state": "thinking"}, "Claude is answering..."),
        ({"tray_state": "speaking"}, "Claude is answering..."),
        ({"tray_state": "ready", "last_text_snippet": "hello world"}, "Copied: hello world"),
        ({"tray_state": "warning", "audio": "down"}, "WSL audio stopped"),
        ({"tray_state": "warning", "mic_count": 0}, "No microphone"),
        ({"tray_state": "error", "detail": "Engine failed 5 times"}, "Engine failed 5 times"),
        ({"tray_state": "idle", "status_text": "From the core"}, "From the core"),
    ],
)
def test_tooltip_and_status_line_per_state(
    ui: CompanionUi,
    fake: FakeController,
    process_events: Callable[..., bool],
    changes: dict[str, object],
    expected: str,
) -> None:
    fake.publish(**changes)
    process_events()
    tray = _tray(ui)
    assert tray.tray.toolTip() == f"VoiceMate\n{expected}"
    assert tray.status_action.text() == expected
    assert not tray.status_action.isEnabled()


def test_recording_tooltip_counts_the_elapsed_time(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(tray_state="recording", recording_since=time.time() - 12.4, flow="clipboard")
    process_events()
    assert _tray(ui).tray.toolTip() == "VoiceMate\nRecording 00:12"


def test_menu_layout_when_idle(ui: CompanionUi) -> None:
    menu = _tray(ui).menu
    assert _texts(menu) == [
        "Listening for Ctrl+Alt+V",
        "Open VoiceMate",
        "Dictate\tCtrl+Alt+V",
        "Ask Claude\tCtrl+Alt+A",
        "Cancel",
        "Recent",
        "Not copied",
        "Restart WSL now...",
        "Mute sounds",
        "Engine",
        "Settings...",
        "Quit VoiceMate",
    ]
    visible = [action.text() for action in menu.actions() if action.isVisible() and not action.isSeparator()]
    assert "Not copied" not in visible and "Restart WSL now..." not in visible
    assert not _action(menu, "Cancel").isEnabled()
    assert _action(menu, "Dictate").isEnabled()


def test_flow_items_become_stop_items_while_recording(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(tray_state="recording", recording_since=time.time(), flow="clipboard")
    process_events()
    menu = _tray(ui).menu
    assert _action(menu, "Stop and copy").text() == "Stop and copy\tCtrl+Alt+V"
    assert _action(menu, "Stop and ask Claude").isEnabled()
    assert _action(menu, "Cancel").isEnabled()
    fake.publish(tray_state="transcribing", recording_since=None)
    process_events()
    assert _action(menu, "Dictate").isVisible()
    assert _action(menu, "Cancel").isEnabled()


def test_flow_items_are_disabled_until_the_engine_is_ready(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.publish(tray_state="starting", supervisor="starting", engine_ready=False)
    process_events()
    assert not _action(_tray(ui).menu, "Dictate").isEnabled()


def test_menu_actions_call_the_controller(ui: CompanionUi, fake: FakeController) -> None:
    menu = _tray(ui).menu
    _action(menu, "Dictate").trigger()
    _action(menu, "Ask Claude").trigger()
    tray = _tray(ui)
    tray.cancel_action.setEnabled(True)
    tray.cancel_action.trigger()
    tray.restart_engine_action.trigger()
    tray.restart_wsl_action.trigger()
    tray.open_logs_action.trigger()
    assert fake.called("toggle") == [("clipboard",), ("claude_chat",)]
    assert fake.called("cancel") == [()]
    assert fake.called("restart_engine") == [()]
    assert fake.called("restart_wsl") == [()]
    assert fake.called("open_logs") == [()]


def test_engine_submenu_hides_restart_wsl_outside_wsl2(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    from app.companion.contract import CompanionSettings

    fake = FakeController(settings=CompanionSettings(engine_mode="external"))
    companion = CompanionUi(fake, tray_available=True, exit_app=lambda: None)
    companion.bridge.attach()
    fake.start()
    process_events()
    assert not _tray(companion).restart_wsl_action.isVisible()
    companion.bridge.detach()


def test_pending_and_wsl_restart_items_follow_the_snapshot(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    item = fake.add_pending("never reached the clipboard")
    fake.publish(pending_wsl_restart=True)
    process_events()
    tray = _tray(ui)
    assert tray.pending_menu.menuAction().isVisible()
    assert tray.pending_menu.title() == "Not copied (1)"
    assert tray.wsl_restart_action.isVisible()
    tray.pending_menu.aboutToShow.emit()
    entries = tray.pending_menu.actions()
    assert [action.text() for action in entries] == ["never reached the clipboard"]
    entries[0].trigger()
    assert fake.called("copy_result") == [(item.instance, item.record["result_seq"])]
    process_events()
    assert not tray.pending_menu.menuAction().isVisible()


def test_recent_submenu_lists_results_and_copies_on_click(ui: CompanionUi, fake: FakeController) -> None:
    tray = _tray(ui)
    tray.recent_menu.aboutToShow.emit()
    entries = tray.recent_menu.actions()
    recent = fake.recent_results()
    assert len(entries) == len(recent)
    entries[1].trigger()
    assert fake.called("copy_result") == [(recent[1].instance, recent[1].record["result_seq"])]


def test_recent_submenu_says_when_it_is_empty(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    fake = FakeController()
    fake._recent.clear()
    companion = CompanionUi(fake, tray_available=True, exit_app=lambda: None)
    companion.bridge.attach()
    tray = _tray(companion)
    tray.recent_menu.aboutToShow.emit()
    assert [(a.text(), a.isEnabled()) for a in tray.recent_menu.actions()] == [("No transcriptions yet", False)]
    companion.bridge.detach()


def test_mute_sounds_applies_the_settings(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    tray = _tray(ui)
    assert not tray.mute_action.isChecked()
    tray.mute_action.trigger()
    assert process_events(lambda: bool(fake.called("apply_settings")))
    (applied,) = fake.called("apply_settings")[0]
    assert applied.cues_enabled is False  # type: ignore[attr-defined]
    assert process_events(lambda: ui.settings().cues_enabled is False)
    tray.menu.aboutToShow.emit()
    assert tray.mute_action.isChecked()


def test_mute_failure_notifies_and_restores_the_check(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.apply_errors = ["Could not write the settings file."]
    tray = _tray(ui)
    tray.mute_action.trigger()
    assert process_events(lambda: bool(ui.notifications.shown))
    assert ui.notifications.shown[-1].message == "Could not write the settings file."
    assert not tray.mute_action.isChecked()


def test_left_click_opens_the_status_window(ui: CompanionUi) -> None:
    _tray(ui).tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert ui.status_window.isVisible()


def test_quit_from_the_menu_waits_for_the_controller(
    ui: CompanionUi, fake: FakeController, exits: list[int], process_events: Callable[..., bool]
) -> None:
    fake.quit_delay = 0.2
    _tray(ui).quit_action.trigger()
    assert fake.called("quit") == [()]
    assert exits == []
    assert "Quitting..." in _tray(ui).tray.toolTip()
    assert all(not action.isEnabled() for action in _tray(ui).menu.actions())
    assert process_events(lambda: exits == [0])
    _tray(ui).quit_action.trigger()  # a second Quit is ignored
    assert fake.called("quit") == [()]
