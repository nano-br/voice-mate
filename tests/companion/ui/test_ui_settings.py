from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import replace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

from app.companion.contract import (  # noqa: E402
    CompanionSettings,
    CueSettings,
    FlowEntry,
    HotkeyBinding,
)
from app.companion.ui.app import CompanionUi  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402
from app.companion.ui.settings_window import GeneralPage, SettingsDialog  # noqa: E402

Mod = Qt.KeyboardModifier


def _open(ui: CompanionUi) -> SettingsDialog:
    ui.show_settings()
    assert ui.settings_dialog is not None
    return ui.settings_dialog


def _select_data(combo: object, value: str) -> None:
    index = combo.findData(value)  # type: ignore[attr-defined]
    assert index >= 0, value
    combo.setCurrentIndex(index)  # type: ignore[attr-defined]


def _status(dialog: SettingsDialog) -> list[str]:
    return [row.status.text() for row in dialog.hotkeys_page.rows]


def test_round_trip_sends_every_edit_to_apply_settings(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    before = fake.settings()
    dialog = _open(ui)
    assert not dialog.apply_button.isEnabled()  # nothing edited yet

    # Hotkeys: capture a new chord for the first flow.
    edit = dialog.hotkeys_page.rows[0].edit
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    QTest.keyClick(edit, Qt.Key.Key_D, Mod.ControlModifier | Mod.ShiftModifier)
    assert process_events(lambda: fake.called("suspend_hotkeys") == [(True,), (False,)])
    assert process_events(lambda: _status(dialog)[0] == "Available")
    assert ("ctrl+shift+d", "clipboard") in fake.called("check_hotkey")

    # Sounds.
    sounds = dialog.sounds_page
    sounds.master_volume.setValue(50)
    _select_data(sounds.rows["start"].preset, "soft")
    sounds.rows["start"].volume.setValue(40)
    _select_data(sounds.rows["ready"].source, "file")
    sounds.rows["ready"].file.setText("C:/sounds/done.wav")
    sounds.rows["error"].enabled.setChecked(False)

    # General.
    general = dialog.general_page
    _select_data(general.language, "es")
    _select_data(general.notify_level, "errors")
    general.start_at_login.setChecked(True)
    general.engine_dir.setText("  custom/voice-mate ")
    _select_data(general.wsl_restart_policy, "ask")
    assert dialog.apply_button.isEnabled()

    dialog.ok_button.click()
    assert process_events(lambda: not dialog.isVisible())
    (sent,) = fake.called("apply_settings")[0]
    cues = dict(before.cues)
    cues["start"] = CueSettings(preset="soft", volume=0.4)
    cues["ready"] = CueSettings(source="file", file="C:/sounds/done.wav")
    cues["error"] = CueSettings(enabled=False)
    expected = replace(
        before,
        hotkeys=(HotkeyBinding("clipboard", "ctrl+shift+d"), HotkeyBinding("claude_chat", "ctrl+alt+a")),
        master_volume=0.5,
        cues=cues,
        language="es",
        notify_level="errors",
        start_at_login=True,
        engine_dir="custom/voice-mate",
        wsl_restart_policy="ask",
    )
    assert sent == expected
    assert ui.settings() == expected


def test_errors_from_apply_settings_stay_visible(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.apply_errors = ["The engine folder cannot contain quotes.", "F12 is reserved by Windows."]
    dialog = _open(ui)
    dialog.general_page.engine_dir.setText('bad"dir')
    dialog.ok_button.click()
    assert process_events(lambda: "F12 is reserved" in dialog.error_text)
    assert "The settings were not saved:" in dialog.error_text
    assert "The engine folder cannot contain quotes." in dialog.error_text
    assert dialog.isVisible()
    assert dialog.apply_button.isEnabled() and dialog.ok_button.isEnabled()
    assert ui.settings() == fake.settings()  # nothing applied

    fake.apply_errors = []
    dialog.apply_button.click()
    assert process_events(lambda: dialog.error_text == "Settings saved.")
    assert dialog.isVisible()
    assert not dialog.apply_button.isEnabled()
    assert ui.settings().engine_dir == 'bad"dir'


def test_ok_without_changes_closes_without_applying(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    dialog.ok_button.click()
    assert not dialog.isVisible()
    assert fake.called("apply_settings") == []


def test_escape_cancels_the_capture_but_not_the_dialog(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    edit = dialog.hotkeys_page.rows[1].edit
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert dialog.isVisible()
    assert edit.chord == "ctrl+alt+a"
    assert process_events(lambda: fake.called("suspend_hotkeys") == [(True,), (False,)])


def test_closing_the_dialog_while_capturing_resumes_the_hotkeys(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    dialog.hotkeys_page.rows[0].edit.start_capture()
    dialog.cancel_button.click()
    assert not dialog.isVisible()
    assert process_events(lambda: fake.called("suspend_hotkeys") == [(True,), (False,)])


def test_duplicates_are_flagged_and_a_swap_is_accepted(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    first, second = dialog.hotkeys_page.rows
    second.edit.set_chord("ctrl+alt+v")
    second.edit.chord_changed.emit("ctrl+alt+v")
    assert _status(dialog) == ["Used by another action", "Used by another action"]
    # Swap: the controller still sees the SAVED bindings and says "duplicate"; the
    # window knows the other row moved away.
    first.edit.set_chord("ctrl+alt+a")
    first.edit.chord_changed.emit("ctrl+alt+a")
    assert process_events(lambda: _status(dialog) == ["Available", "Available"])


def test_hotkey_check_results_are_shown(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.hotkey_results = {"ctrl+alt+x": "in_use", "f12": "invalid"}
    dialog = _open(ui)
    first, second = dialog.hotkeys_page.rows
    first.edit.set_chord("ctrl+alt+x")
    first.edit.chord_changed.emit("ctrl+alt+x")
    second.edit.set_chord("f12")
    second.edit.chord_changed.emit("f12")
    assert process_events(lambda: _status(dialog) == ["Used by another app", "Not allowed"])
    assert "F12" in second.status.toolTip()
    second.edit.set_chord("")
    second.edit.chord_changed.emit("")
    assert _status(dialog)[1] == "No shortcut: use the menu"


def test_restore_defaults(ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]) -> None:
    dialog = _open(ui)
    for row in dialog.hotkeys_page.rows:
        row.edit.set_chord("f9")
    dialog.hotkeys_page.restore_defaults()
    assert [row.edit.chord for row in dialog.hotkeys_page.rows] == ["ctrl+alt+v", "ctrl+alt+a"]


def test_engine_owned_hotkeys_are_read_only(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    flows = (FlowEntry("clipboard", "clipboard", "ctrl+shift+space"), FlowEntry("claude_chat", "claude_chat", "f9"))
    fake = FakeController(flows=flows)
    companion = CompanionUi(fake, tray_available=False, exit_app=lambda: None)
    companion.bridge.attach()
    fake.start()
    fake.publish(hotkeys_owned_by_engine=True)
    process_events()
    dialog = _open(companion)
    page = dialog.hotkeys_page
    assert [row.edit.text() for row in page.rows] == ["Ctrl+Shift+Space", "F9"]
    assert _status(dialog) == ["Set by the engine", "Set by the engine"]
    assert page.engine_note.isVisibleTo(dialog) and not page.defaults_button.isVisibleTo(dialog)
    QTest.mouseClick(page.rows[0].edit, Qt.MouseButton.LeftButton)
    assert not page.rows[0].edit.capturing
    assert fake.called("suspend_hotkeys") == []
    assert page.collect(fake.settings().hotkeys) == fake.settings().hotkeys
    dialog.hide()
    companion.bridge.detach()


def test_settings_from_a_newer_version_are_read_only(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    fake = FakeController(settings=CompanionSettings(version=99))
    companion = CompanionUi(fake, tray_available=False, exit_app=lambda: None)
    companion.bridge.attach()
    dialog = _open(companion)
    assert dialog.read_only_note.isVisibleTo(dialog)
    assert not dialog.ok_button.isEnabled() and not dialog.apply_button.isEnabled()
    assert not dialog.sounds_page.isEnabled() and not dialog.general_page.isEnabled()
    assert not dialog.hotkeys_page.defaults_button.isEnabled()
    assert dialog.hotkeys_page.isEnabled()  # the page itself stays enabled (rows refuse captures)
    QTest.mouseClick(dialog.hotkeys_page.rows[0].edit, Qt.MouseButton.LeftButton)
    assert not dialog.hotkeys_page.rows[0].edit.capturing
    dialog.hide()
    companion.bridge.detach()


def test_preview_plays_the_edited_cue_at_the_edited_master_volume(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    sounds = dialog.sounds_page
    sounds.master_volume.setValue(30)
    _select_data(sounds.rows["ready"].preset, "click")
    sounds.rows["ready"].volume.setValue(70)
    sounds.rows["ready"].enabled.setChecked(False)  # preview still plays a disabled cue
    sounds.rows["ready"].preview.click()
    assert process_events(lambda: bool(fake.called("preview_cue")))
    assert fake.called("preview_cue") == [("ready", CueSettings(enabled=False, preset="click", volume=0.7), 0.3)]


def test_sound_rows_follow_their_toggles(ui: CompanionUi) -> None:
    dialog = _open(ui)
    sounds = dialog.sounds_page
    row = sounds.rows["warning"]
    _select_data(row.source, "file")
    assert row.stack.currentIndex() == 1
    row.enabled.setChecked(False)
    assert not row.volume.isEnabled()
    assert row.preview.isEnabled()
    sounds.cues_enabled.setChecked(False)
    other = sounds.rows["ready"]
    assert not other.enabled.isEnabled() and not other.volume.isEnabled() and not other.source.isEnabled()
    # Preview plays even when the sounds are muted (contract): it stays available.
    assert all(cue_row.preview.isEnabled() for cue_row in sounds.rows.values())
    sounds.cues_enabled.setChecked(True)
    assert other.volume.isEnabled() and not row.volume.isEnabled()


def test_wsl_fields_follow_the_engine_mode(ui: CompanionUi) -> None:
    dialog = _open(ui)
    general = dialog.general_page
    _select_data(general.engine_mode, "external")
    assert not general.wsl_distro.isEnabled()
    assert not general.engine_dir.isEnabled()
    assert not general.wsl_restart_policy.isEnabled()


def test_reopening_reloads_the_applied_settings(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    dialog.general_page.engine_dir.setText("edited but cancelled")
    dialog.cancel_button.click()
    ui.show_settings()
    assert ui.settings_dialog is dialog
    assert dialog.general_page.engine_dir.text() == fake.settings().engine_dir


def test_a_cleared_shortcut_drops_its_binding(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    """No binding = no hotkey for that flow. An empty chord would be refused by the core."""
    dialog = _open(ui)
    edit = dialog.hotkeys_page.rows[1].edit
    edit.setFocus()
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    assert edit.chord == ""
    dialog.ok_button.click()
    assert process_events(lambda: not dialog.isVisible())
    (sent,) = fake.called("apply_settings")[0]
    assert sent.hotkeys == (HotkeyBinding("clipboard", "ctrl+alt+v"),)  # type: ignore[attr-defined]
    assert ui.settings().hotkeys == (HotkeyBinding("clipboard", "ctrl+alt+v"),)
    # The flow is still offered, so it can get a shortcut again.
    ui.show_settings()
    assert [row.flow.name for row in dialog.hotkeys_page.rows] == ["clipboard", "claude_chat"]
    assert dialog.hotkeys_page.rows[1].edit.chord == ""


def test_a_flow_without_a_saved_binding_is_not_sent_empty(
    qapp: QApplication, process_events: Callable[..., bool]
) -> None:
    flows = (*FakeController().snapshot().flows, FlowEntry("notes", "clipboard", "ctrl+alt+n"))
    fake = FakeController(flows=())
    companion = CompanionUi(fake, tray_available=False, exit_app=lambda: None)
    companion.bridge.attach()
    fake.start()
    fake.publish(flows=flows)  # the engine reports a flow the settings never bound
    process_events()
    dialog = _open(companion)
    assert [row.flow.name for row in dialog.hotkeys_page.rows] == ["clipboard", "claude_chat", "notes"]
    dialog.general_page.engine_dir.setText("elsewhere")
    dialog.ok_button.click()
    assert process_events(lambda: not dialog.isVisible())
    assert fake.called("apply_settings")
    assert all(binding.chord for binding in fake.settings().hotkeys)
    dialog.hide()
    companion.bridge.detach()


def test_apply_keeps_what_the_core_changed_meanwhile(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    # The core changes settings while the window is open (a detected engine folder, a
    # client key, another cue).
    current = fake.settings()
    cues = dict(current.cues)
    cues["ready"] = CueSettings(preset="click")
    fake.set_settings(replace(current, engine_dir="detected/voice-mate", client_key="k-1", cues=cues))
    process_events()
    _select_data(dialog.sounds_page.rows["start"].preset, "soft")
    dialog.ok_button.click()
    assert process_events(lambda: not dialog.isVisible())
    (sent,) = fake.called("apply_settings")[0]
    assert sent.engine_dir == "detected/voice-mate"  # type: ignore[attr-defined]
    assert sent.client_key == "k-1"  # type: ignore[attr-defined]
    assert sent.cues["ready"] == CueSettings(preset="click")  # type: ignore[attr-defined]
    assert sent.cues["start"] == CueSettings(preset="soft")  # type: ignore[attr-defined]


def test_apply_shows_what_the_core_normalized(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    dialog = _open(ui)
    dialog.general_page.engine_dir.setText("~/projects/voice-mate")
    dialog.apply_button.click()
    assert process_events(lambda: dialog.error_text == "Settings saved.")
    assert dialog.general_page.engine_dir.text() == "projects/voice-mate"
    assert not dialog.apply_button.isEnabled()


def test_errors_reopen_a_dialog_closed_while_saving(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.apply_errors = ["Could not save the settings file."]
    dialog = _open(ui)
    dialog.general_page.engine_dir.setText("x")
    dialog.apply_button.click()
    dialog.hide()  # closed before the answer came back
    assert process_events(lambda: dialog.isVisible())
    assert "Could not save the settings file." in dialog.error_text
    assert dialog.general_page.engine_dir.text() == "x"  # the edit is still there


def test_pages_are_locked_while_saving(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reload at the end of a save would silently drop an edit made meanwhile."""
    release = threading.Event()
    original = fake.apply_settings

    def slow_apply(settings: CompanionSettings) -> list[str]:
        release.wait(5)
        return original(settings)

    monkeypatch.setattr(fake, "apply_settings", slow_apply)
    dialog = _open(ui)
    pages = (dialog.hotkeys_page, dialog.sounds_page, dialog.general_page)
    dialog.general_page.engine_dir.setText("custom/voice-mate")
    dialog.apply_button.click()
    process_events()
    assert dialog.error_text == "Saving..."
    assert not any(page.isEnabled() for page in pages)
    assert not dialog.apply_button.isEnabled() and not dialog.ok_button.isEnabled()
    release.set()
    assert process_events(lambda: dialog.error_text == "Settings saved.")
    assert all(page.isEnabled() for page in pages)
    assert dialog.ok_button.isEnabled() and not dialog.apply_button.isEnabled()
    assert dialog.general_page.engine_dir.text() == "custom/voice-mate"
    assert dialog.hotkeys_page.defaults_button.isEnabled()


def test_a_failed_save_unlocks_the_pages_with_the_edits_kept(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    fake.apply_errors = ["Could not save the settings file."]
    dialog = _open(ui)
    dialog.general_page.engine_dir.setText("x")
    dialog.apply_button.click()
    assert process_events(lambda: "Could not save the settings file." in dialog.error_text)
    assert dialog.general_page.isEnabled() and dialog.sounds_page.isEnabled() and dialog.hotkeys_page.isEnabled()
    assert dialog.general_page.engine_dir.text() == "x"


def test_volumes_off_the_slider_grid_are_not_edits(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    cues = dict(CompanionSettings().cues)
    cues["start"] = CueSettings(volume=0.333)
    fake = FakeController(settings=CompanionSettings(master_volume=0.555, cues=cues))
    companion = CompanionUi(fake, tray_available=False, exit_app=lambda: None)
    companion.bridge.attach()
    dialog = _open(companion)
    assert not dialog.is_dirty()
    assert not dialog.apply_button.isEnabled()
    dialog.sounds_page.rows["start"].volume.setValue(40)
    assert dialog.collect().cues["start"].volume == 0.4
    assert dialog.collect().master_volume == 0.555
    dialog.hide()
    companion.bridge.detach()


def test_rebase_settings_merges_per_field_flow_and_cue() -> None:
    from app.companion.ui.settings_window import rebase_settings

    baseline = CompanionSettings()
    edited = replace(
        baseline,
        language="es",
        hotkeys=(HotkeyBinding("clipboard", "f9"),),  # claude_chat cleared, clipboard changed
    )
    current = replace(
        baseline,
        engine_dir="detected",
        hotkeys=(*baseline.hotkeys, HotkeyBinding("notes", "ctrl+alt+n")),
    )
    rebased = rebase_settings(edited, baseline, current)
    assert rebased.language == "es"
    assert rebased.engine_dir == "detected"
    assert rebased.hotkeys == (HotkeyBinding("clipboard", "f9"), HotkeyBinding("notes", "ctrl+alt+n"))
    assert rebase_settings(baseline, baseline, current) == current


def test_problem_statuses_are_drawn_at_full_opacity(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    """A status shown after a neutral one ("Checking...") must not keep the placeholder
    role: Qt would paint the stylesheet color at half opacity (about 2:1 contrast)."""
    from PySide6.QtGui import QColor, QImage, QPalette

    from app.companion.ui.settings_window import _status_color

    fake.hotkey_results = {"ctrl+alt+x": "in_use"}
    dialog = _open(ui)
    row = dialog.hotkeys_page.rows[0]
    row.edit.set_chord("ctrl+alt+x")
    row.edit.chord_changed.emit("ctrl+alt+x")  # neutral "Checking..." first, then the result
    assert process_events(lambda: row.status.text() == "Used by another app")
    assert row.status.foregroundRole() == QPalette.ColorRole.WindowText
    process_events()
    expected = QColor(_status_color(dialog.hotkeys_page, False))
    image = row.status.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    strongest = max(
        (image.pixelColor(x, y) for y in range(image.height()) for x in range(image.width())),
        key=lambda color: (
            -abs(color.red() - expected.red())
            - abs(color.green() - expected.green())
            - abs(color.blue() - expected.blue())
        ),
    )
    distance = (
        abs(strongest.red() - expected.red())
        + abs(strongest.green() - expected.green())
        + abs(strongest.blue() - expected.blue())
    )
    assert distance <= 30, (strongest.name(), expected.name())


def test_errors_do_not_reopen_the_dialog_while_quitting(
    ui: CompanionUi, fake: FakeController, exits: list[int], process_events: Callable[..., bool]
) -> None:
    fake.apply_errors = ["Could not save the settings file."]
    fake.quit_delay = 0.5
    dialog = _open(ui)
    dialog.general_page.engine_dir.setText("x")
    dialog.apply_button.click()
    ui.quit_app()  # hides the dialog before the answer comes back
    assert process_events(lambda: bool(fake.called("apply_settings")))
    process_events()
    assert not process_events(lambda: dialog.isVisible(), timeout=0.3)
    assert process_events(lambda: exits == [0], timeout=3.0)  # let the quit finish in this test


def test_tray_icon_checkbox_only_on_windows(qapp: QApplication) -> None:
    hidden = CompanionSettings(tray_icon_visible=False)
    windows = GeneralPage(platform="win32")
    assert windows.tray_icon_visible is not None
    assert windows.tray_icon_visible.text() == "Always show the VoiceMate icon on the taskbar"
    windows.load(hidden)
    assert not windows.tray_icon_visible.isChecked()
    edits: list[None] = []
    windows.changed.connect(lambda: edits.append(None))
    windows.tray_icon_visible.setChecked(True)
    assert edits  # an edit enables Apply
    assert windows.apply_to(hidden).tray_icon_visible is True

    linux = GeneralPage(platform="linux")
    assert linux.tray_icon_visible is None
    assert not any(
        box.text() == "Always show the VoiceMate icon on the taskbar" for box in linux.findChildren(QCheckBox)
    )
    linux.load(hidden)
    assert linux.apply_to(hidden).tray_icon_visible is False  # kept as saved


def test_the_settings_dialog_follows_the_running_os(ui: CompanionUi) -> None:
    dialog = _open(ui)
    assert (dialog.general_page.tray_icon_visible is not None) == (sys.platform == "win32")


@pytest.mark.skipif(sys.platform != "win32", reason="the checkbox exists on Windows only")
def test_bringing_back_the_tray_icon_takes_off_apply_then_on(
    ui: CompanionUi, fake: FakeController, process_events: Callable[..., bool]
) -> None:
    """What the tooltip tells the user: off, Apply, on again (off and on in one edit sends nothing)."""
    dialog = _open(ui)
    checkbox = dialog.general_page.tray_icon_visible
    assert checkbox is not None and checkbox.isChecked()
    assert "turn this off, click Apply and turn it on again" in checkbox.toolTip()
    checkbox.setChecked(False)
    checkbox.setChecked(True)
    assert not dialog.apply_button.isEnabled()  # back to the saved value: nothing to send
    checkbox.setChecked(False)
    dialog.apply_button.click()
    assert process_events(lambda: checkbox.isEnabled())  # locked while saving, like a user sees it
    assert fake.settings().tray_icon_visible is False
    checkbox.setChecked(True)
    assert dialog.apply_button.isEnabled()
    dialog.ok_button.click()
    assert process_events(lambda: len(fake.called("apply_settings")) == 2)
    sent = [call[0] for call in fake.called("apply_settings")]
    assert [s.tray_icon_visible for s in sent if isinstance(s, CompanionSettings)] == [False, True]
    assert fake.settings().tray_icon_visible is True


def test_the_sound_file_picker_works_without_a_home_folder(ui: CompanionUi, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from app.companion.ui import settings_window

    def no_home(cls: type[Path]) -> Path:
        raise RuntimeError("Could not determine home directory.")

    starts: list[str] = []

    def pick(parent: object, caption: str, start: str, filters: str) -> tuple[str, str]:
        starts.append(start)
        return "", ""

    monkeypatch.setattr(Path, "home", classmethod(no_home))
    monkeypatch.setattr(settings_window.QFileDialog, "getOpenFileName", pick)
    sounds = _open(ui).sounds_page
    row = sounds.rows["ready"]
    row.file.setText("")
    sounds._choose_file(row)
    assert starts == [""]  # the dialog opens where Qt wants instead of failing


def test_dialog_never_clips_its_pages(
    ui: CompanionUi, monkeypatch: pytest.MonkeyPatch, process_events: Callable[..., bool]
) -> None:
    """The layout owns the minimum size: longer translations (Russian cue names on the
    Sounds tab) make the window wider instead of squeezing the rows under their text."""
    from app.companion.ui import texts

    long_labels = {cue: f"{label} with a much longer translation" for cue, label in texts.cue_labels().items()}
    monkeypatch.setattr(texts, "cue_labels", lambda: long_labels)
    dialog = _open(ui)
    for index in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(index)
        process_events()
        assert dialog.width() >= dialog.minimumSizeHint().width(), dialog.tabs.tabText(index)
        assert dialog.height() >= dialog.minimumSizeHint().height(), dialog.tabs.tabText(index)
    row = next(iter(dialog.sounds_page.rows.values()))
    assert row.enabled.width() >= row.enabled.sizeHint().width()
