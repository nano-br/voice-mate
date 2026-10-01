from __future__ import annotations

import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QKeyCombination, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402

from app.companion.ui.chords import (  # noqa: E402
    chord_display,
    chord_from_key,
    chord_to_sequence,
    modifiers_display,
    normalize_chord,
    sequence_to_chord,
)

Mod = Qt.KeyboardModifier


@pytest.mark.parametrize(
    "chord",
    ["ctrl+alt+v", "ctrl+alt+a", "ctrl+shift+f5", "f9", "alt+win+space", "ctrl+page up", "shift+1", "ctrl+delete"],
)
def test_chord_round_trips_through_qkeysequence(chord: str) -> None:
    sequence = chord_to_sequence(chord)
    assert not sequence.isEmpty()
    assert sequence_to_chord(sequence) == chord


def test_chord_to_sequence_matches_qt_portable_text() -> None:
    assert chord_to_sequence("ctrl+alt+v") == QKeySequence("Ctrl+Alt+V")
    assert chord_to_sequence("win+f2") == QKeySequence(QKeyCombination(Mod.MetaModifier, Qt.Key.Key_F2))


def test_sequence_to_chord_orders_modifiers_canonically() -> None:
    sequence = QKeySequence(QKeyCombination(Mod.ShiftModifier | Mod.ControlModifier | Mod.AltModifier, Qt.Key.Key_K))
    assert sequence_to_chord(sequence) == "ctrl+alt+shift+k"


def test_empty_or_unknown_chords_give_an_empty_sequence() -> None:
    assert chord_to_sequence("").isEmpty()
    assert chord_to_sequence("ctrl+alt").isEmpty()
    assert chord_to_sequence("ctrl+launchpad").isEmpty()
    assert sequence_to_chord(QKeySequence()) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ctrl+Alt+V", "ctrl+alt+v"),
        ("alt+control+v", "ctrl+alt+v"),
        ("windows+shift+PgUp", "shift+win+page up"),
        ("super+Return", "win+enter"),
        (" ctrl + escape ", "ctrl+esc"),
        ("", ""),
    ],
)
def test_normalize_chord(raw: str, expected: str) -> None:
    assert normalize_chord(raw) == expected


def test_chord_from_key_builds_the_stored_format() -> None:
    assert chord_from_key(int(Qt.Key.Key_V), Mod.ControlModifier | Mod.AltModifier) == "ctrl+alt+v"
    assert chord_from_key(int(Qt.Key.Key_F7), Mod.NoModifier) == "f7"
    assert chord_from_key(int(Qt.Key.Key_Space), Mod.MetaModifier) == "win+space"
    assert chord_from_key(int(Qt.Key.Key_PageDown), Mod.ControlModifier) == "ctrl+page down"


def test_chord_from_key_refuses_modifiers_alone_and_unsupported_keys() -> None:
    assert chord_from_key(int(Qt.Key.Key_Control), Mod.ControlModifier) is None
    assert chord_from_key(int(Qt.Key.Key_Alt), Mod.AltModifier | Mod.ControlModifier) is None
    assert chord_from_key(int(Qt.Key.Key_Comma), Mod.ControlModifier) is None  # layout dependent
    # Keypad digits are other virtual keys than the top-row digits.
    assert chord_from_key(int(Qt.Key.Key_5), Mod.ControlModifier | Mod.KeypadModifier) is None


@pytest.mark.skipif(sys.platform != "win32", reason="the virtual key is only read on Windows")
def test_shifted_digits_use_the_virtual_key_on_windows() -> None:
    # Shift+1 arrives as Key_Exclam on a US layout; RegisterHotKey needs VK "1" (0x31).
    assert chord_from_key(int(Qt.Key.Key_Exclam), Mod.ControlModifier | Mod.ShiftModifier, 0x31) == "ctrl+shift+1"


def test_chord_display_uses_key_caps() -> None:
    win = "Win" if sys.platform == "win32" else "Super"
    assert chord_display("ctrl+alt+v") == "Ctrl+Alt+V"
    assert chord_display("ctrl+page up") == "Ctrl+Page Up"
    assert chord_display("win+f12") == f"{win}+F12"
    assert chord_display("") == ""
    assert modifiers_display(Mod.ControlModifier | Mod.AltModifier) == "Ctrl+Alt+"
    assert modifiers_display(Mod.NoModifier) == ""
