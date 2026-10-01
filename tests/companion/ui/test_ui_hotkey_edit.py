from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout  # noqa: E402

from app.companion.ui.hotkey_edit import HotkeyEdit  # noqa: E402

Mod = Qt.KeyboardModifier


@pytest.fixture
def dialog(qapp: QApplication, process_events: Callable[..., bool]) -> Iterator[tuple[QDialog, HotkeyEdit, list[str]]]:
    window = QDialog()
    layout = QVBoxLayout(window)
    edit = HotkeyEdit("ctrl+alt+v")
    layout.addWidget(edit)
    events: list[str] = []
    edit.capture_started.connect(lambda: events.append("started"))
    edit.capture_finished.connect(lambda: events.append("finished"))
    edit.chord_changed.connect(lambda chord: events.append(f"changed:{chord}"))
    window.show()
    edit.setFocus()
    process_events()
    yield window, edit, events
    window.hide()


def test_shows_the_chord_as_key_caps(dialog: tuple[QDialog, HotkeyEdit, list[str]]) -> None:
    _window, edit, _events = dialog
    assert edit.text() == "Ctrl+Alt+V"
    assert not edit.capturing


def test_click_then_chord_captures_one_combination(dialog: tuple[QDialog, HotkeyEdit, list[str]]) -> None:
    _window, edit, events = dialog
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    assert edit.capturing
    assert edit.text() == ""
    QTest.keyPress(edit, Qt.Key.Key_Control, Mod.ControlModifier)
    assert edit.text() == "Ctrl+"
    QTest.keyClick(edit, Qt.Key.Key_D, Mod.ControlModifier | Mod.ShiftModifier)
    assert not edit.capturing
    assert edit.chord == "ctrl+shift+d"
    assert edit.text() == "Ctrl+Shift+D"
    assert events == ["started", "finished", "changed:ctrl+shift+d"]


def test_enter_starts_capturing_and_escape_cancels_without_closing_the_dialog(
    dialog: tuple[QDialog, HotkeyEdit, list[str]],
) -> None:
    window, edit, events = dialog
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert edit.capturing
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert not edit.capturing
    assert edit.chord == "ctrl+alt+v"
    assert window.isVisible()
    assert events == ["started", "finished"]


def test_backspace_clears(dialog: tuple[QDialog, HotkeyEdit, list[str]]) -> None:
    _window, edit, events = dialog
    QTest.keyClick(edit, Qt.Key.Key_Space)
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    assert edit.chord == ""
    assert edit.text() == ""
    assert edit.placeholderText() == "No shortcut"
    assert events[-1] == "changed:"
    # Not capturing: Delete also clears, and only emits when something changes.
    edit.set_chord("f9")
    QTest.keyClick(edit, Qt.Key.Key_Delete)
    assert edit.chord == ""


def test_unsupported_keys_keep_capturing(dialog: tuple[QDialog, HotkeyEdit, list[str]]) -> None:
    _window, edit, _events = dialog
    edit.start_capture()
    QTest.keyClick(edit, Qt.Key.Key_VolumeUp, Mod.ControlModifier)
    assert edit.capturing
    assert edit.placeholderText() == "This key cannot be used"
    QTest.keyClick(edit, Qt.Key.Key_F8)
    assert edit.chord == "f8"


def test_focus_loss_cancels_the_capture(
    dialog: tuple[QDialog, HotkeyEdit, list[str]], process_events: Callable[..., bool]
) -> None:
    window, edit, events = dialog
    other = HotkeyEdit("f2")
    layout = window.layout()
    assert layout is not None
    layout.addWidget(other)
    other.show()
    edit.setFocus()
    process_events()
    edit.start_capture()
    other.setFocus()
    process_events(lambda: not edit.capturing)
    assert not edit.capturing
    assert edit.chord == "ctrl+alt+v"
    assert events == ["started", "finished"]


def test_shortcut_overrides_are_swallowed_while_capturing(dialog: tuple[QDialog, HotkeyEdit, list[str]]) -> None:
    _window, edit, _events = dialog
    edit.start_capture()
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_S, Mod.AltModifier)
    QApplication.sendEvent(edit, override)
    assert override.isAccepted()


def test_read_only_never_captures(dialog: tuple[QDialog, HotkeyEdit, list[str]]) -> None:
    _window, edit, events = dialog
    edit.set_editable(False)
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    QTest.keyClick(edit, Qt.Key.Key_Return)
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    assert not edit.capturing
    assert edit.chord == "ctrl+alt+v"
    assert events == []
