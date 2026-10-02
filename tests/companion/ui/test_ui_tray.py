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


def _planes(state: TrayState, tone: icons.GlyphTone, size: int) -> list[tuple[int, int]]:
    """(lightness, alpha) per pixel: what is left of the icon without colors."""
    image = icons.tray_pixmap(state, tone, size).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    return [
        (image.pixelColor(x, y).lightness(), image.pixelColor(x, y).alpha())
        for y in range(image.height())
        for x in range(image.width())
    ]


def _visible_difference(first: list[tuple[int, int]], second: list[tuple[int, int]]) -> int:
    """Pixels that differ clearly: a big step in lightness, or in coverage (the silhouette)."""
    return sum(
        1
        for (light_a, alpha_a), (light_b, alpha_b) in zip(first, second, strict=True)
        if abs(alpha_a - alpha_b) >= 128 or (min(alpha_a, alpha_b) >= 128 and abs(light_a - light_b) >= 64)
    )


def _silhouette(state: TrayState, size: int) -> list[bool]:
    image = icons.tray_pixmap(state, "light", size).toImage().convertToFormat(QImage.Format.Format_ARGB32)
    return [image.pixelColor(x, y).alpha() >= 128 for y in range(image.height()) for x in range(image.width())]


# At 16 px a badge is about 9 x 9 pixels: ask for a clear difference in at least 8 of them.
_MIN_DIFFERENT_PIXELS = {16: 8, 32: 32}


@pytest.mark.parametrize("size", [16, 32])
@pytest.mark.parametrize("tone", ["light", "dark"])
def test_every_tray_state_is_distinguishable_without_color(
    qapp: QApplication, tone: icons.GlyphTone, size: int
) -> None:
    planes = {state: _planes(state, tone, size) for state in ALL_STATES}
    for first, second in itertools.combinations(ALL_STATES, 2):
        different = _visible_difference(planes[first], planes[second])
        assert different >= _MIN_DIFFERENT_PIXELS[size], f"{first}/{second}: {different} px at {size} px"


@pytest.mark.parametrize(("first", "second"), [("stopped", "idle"), ("starting", "restarting")])
@pytest.mark.parametrize("size", [16, 32])
def test_related_states_differ_by_badge_shape(
    qapp: QApplication, first: TrayState, second: TrayState, size: int
) -> None:
    assert _silhouette(first, size) != _silhouette(second, size)


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
    tray.open_logs_action.trigger()
    assert fake.called("toggle") == [("clipboard",), ("claude_chat",)]
    assert fake.called("cancel") == [()]
    assert fake.called("restart_engine") == [()]
    assert fake.called("open_logs") == [()]


@pytest.mark.parametrize(("answer", "restarts"), [("Restart WSL", [()]), ("Cancel", [])])
def test_restart_wsl_from_the_menu_asks_first(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool], answer: str, restarts: list[tuple[()]]
) -> None:
    _tray(ui).restart_wsl_action.trigger()
    box = ui._restart_wsl_box
    assert box is not None and box.isVisible()
    assert "Docker" in box.text()
    default = box.defaultButton()
    assert default is not None and default.text() == "Cancel"  # Enter never stops every distro
    assert fake.called("restart_wsl") == []
    next(button for button in box.buttons() if button.text() == answer).click()
    assert process_events(lambda: ui._restart_wsl_box is None)
    assert fake.called("restart_wsl") == restarts


def test_engine_submenu_hides_restart_wsl_outside_wsl2(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    from app.companion.contract import CompanionSettings

    fake = FakeController(settings=CompanionSettings(engine_mode="external"))
    companion = CompanionUi(fake, tray_available=True, exit_app=lambda: None)
    companion.bridge.attach()
    fake.start()
    process_events()
    assert not _tray(companion).restart_wsl_action.isVisible()
    companion.confirm_restart_wsl()  # e.g. a stale jump list task
    assert companion._restart_wsl_box is None
    companion.bridge.detach()


def test_menu_reads_the_live_settings_before_showing(ui: CompanionUi, fake: FakeController) -> None:
    """Settings changes do not come with a snapshot: the menu re-reads them when it opens."""
    from dataclasses import replace

    from app.companion.contract import HotkeyBinding

    tray = _tray(ui)
    current = fake.settings()
    with fake._lock:  # changed behind the UI's back (no snapshot published)
        fake._settings = replace(
            current, engine_mode="external", hotkeys=(HotkeyBinding("clipboard", "f9"),), cues_enabled=False
        )
    tray.menu.aboutToShow.emit()
    assert not tray.restart_wsl_action.isVisible()
    assert tray.mute_action.isChecked()
    assert _action(tray.menu, "Dictate").text() == "Dictate\tF9"
    assert _action(tray.menu, "Ask Claude").text() == "Ask Claude"  # no binding: no hotkey shown


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
    assert [action.text() for action in entries if not action.isSeparator()] == [
        "never reached the clipboard",
        "Clear list",
    ]
    assert entries[1].isSeparator()
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


def test_mute_never_overwrites_what_the_core_changed(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    """The core filled in a detected engine_dir after the UI started: Mute must keep it."""
    from dataclasses import replace

    fake.set_settings(replace(fake.settings(), engine_dir="detected/voice-mate", client_key="k-123"))
    process_events()
    _tray(ui).mute_action.trigger()
    assert process_events(lambda: bool(fake.called("apply_settings")))
    (applied,) = fake.called("apply_settings")[0]
    assert applied == replace(fake.settings(), cues_enabled=False)  # type: ignore[comparison-overlap]
    assert (applied.engine_dir, applied.client_key) == ("detected/voice-mate", "k-123")  # type: ignore[attr-defined]


def test_mute_is_disabled_for_settings_from_a_newer_version(
    qapp: QApplication, process_events: Callable[..., bool]
) -> None:
    from app.companion.contract import CompanionSettings

    fake = FakeController(settings=CompanionSettings(version=99))
    companion = CompanionUi(fake, tray_available=True, exit_app=lambda: None)
    companion.bridge.attach()
    tray = _tray(companion)
    tray.menu.aboutToShow.emit()
    assert not tray.mute_action.isEnabled()
    companion.bridge.detach()


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


def test_rebuilt_flow_actions_stay_disabled_until_the_engine_is_ready(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    from app.companion.contract import FlowEntry

    fake.publish(tray_state="starting", supervisor="starting", engine_ready=False)
    process_events()
    flows = (*fake.snapshot().flows, FlowEntry("notes", "clipboard", "ctrl+alt+n"))
    fake.publish(flows=flows)  # the flows change: the actions are rebuilt
    process_events()
    tray = _tray(ui)
    assert len(tray.flow_actions) == 3
    assert not any(action.isEnabled() for action in tray.flow_actions)
    tray.menu.aboutToShow.emit()  # the pre-show refresh must not enable them either
    assert not any(action.isEnabled() for action in tray.flow_actions)


@pytest.mark.parametrize(("answer", "cleared"), [("Clear list", True), ("Cancel", False)])
def test_clear_list_in_the_not_copied_submenu_asks_first(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool], answer: str, cleared: bool
) -> None:
    first = fake.add_pending("first")
    second = fake.add_pending("second")
    process_events()
    seen = tuple((item.instance, item.record["result_seq"]) for item in (first, second))
    clears = [(seen,)] if cleared else []
    tray = _tray(ui)
    tray.pending_menu.aboutToShow.emit()
    _action(tray.pending_menu, "Clear list").trigger()
    box = ui._clear_pending_box
    assert box is not None and box.isVisible()
    assert box.windowTitle() == 'Clear the "Not copied" list?'
    default = box.defaultButton()
    assert default is not None and default.text() == "Cancel"  # Enter never deletes the texts
    _action(tray.pending_menu, "Clear list").trigger()  # a second click raises the same question
    assert ui._clear_pending_box is box
    assert fake.called("clear_pending") == []
    next(button for button in box.buttons() if button.text() == answer).click()
    assert process_events(lambda: ui._clear_pending_box is None)
    assert fake.called("clear_pending") == clears
    process_events()
    assert tray.pending_menu.menuAction().isVisible() is not cleared
