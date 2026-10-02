from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QKeyCombination, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402

from app.companion.ui import chords  # noqa: E402
from app.companion.ui.chords import (  # noqa: E402
    chord_from_key,
    chord_to_sequence,
    display_chord,
    modifiers_display,
    normalize_chord,
    parse_chord,
    sequence_to_chord,
)

Mod = Qt.KeyboardModifier

# Inputs that pin down the chord rules and display shared with app/companion/chords.py.
SAMPLES = (
    "ctrl+alt+v",
    "Ctrl+Alt+V",
    "alt+control+v",
    "windows+shift+PgUp",
    "super+Return",
    " ctrl + escape ",
    "ctrl+page  up",
    "ctrl+print screen",
    "win+num5",
    "ctrl+;",
    "ctrl+shift+'",
    "f9",
    "F24",
    "f12",
    "ctrl+f12",
    "v",
    "shift+a",
    "shift+f2",
    "ctrl+ctrl+v",
    "ctrl+a+b",
    "ctrl+",
    "ctrl+launchpad",
    "",
)


@pytest.mark.parametrize(
    "chord",
    ["ctrl+alt+v", "ctrl+shift+f5", "f9", "alt+win+space", "ctrl+page up", "shift+ctrl+1", "ctrl+delete", "win+num5"],
)
def test_chord_round_trips_through_qkeysequence(chord: str) -> None:
    sequence = chord_to_sequence(chord)
    assert not sequence.isEmpty()
    assert sequence_to_chord(sequence) == normalize_chord(chord)


def test_chord_to_sequence_matches_qt_portable_text() -> None:
    assert chord_to_sequence("ctrl+alt+v") == QKeySequence("Ctrl+Alt+V")
    assert chord_to_sequence("win+f2") == QKeySequence(QKeyCombination(Mod.MetaModifier, Qt.Key.Key_F2))


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
        ("f12", None),  # reserved by Windows
        ("shift+a", None),  # Shift alone would steal typing
        ("v", None),  # needs a modifier
        ("", None),
    ],
)
def test_normalize_chord_follows_the_core_rules(raw: str, expected: str | None) -> None:
    assert normalize_chord(raw) == expected


@pytest.mark.parametrize(
    ("chord", "shown"),
    [
        ("ctrl+alt+v", "Ctrl+Alt+V"),
        ("ctrl+page up", "Ctrl+PageUp"),
        ("ctrl+print screen", "Ctrl+PrintScreen"),
        ("win+f2", "Win+F2"),
        ("win+num5", "Win+Num5"),
        ("ctrl+;", "Ctrl+;"),
        ("v", "v"),  # not a usable chord: shown as is, like the core does
        ("", ""),
    ],
)
def test_display_chord_matches_the_core_names(chord: str, shown: str) -> None:
    assert display_chord(chord) == shown


def test_chord_from_key_builds_the_stored_format() -> None:
    assert chord_from_key(int(Qt.Key.Key_V), Mod.ControlModifier | Mod.AltModifier) == "ctrl+alt+v"
    assert chord_from_key(int(Qt.Key.Key_F7), Mod.NoModifier) == "f7"
    assert chord_from_key(int(Qt.Key.Key_Space), Mod.MetaModifier) == "win+space"
    assert chord_from_key(int(Qt.Key.Key_PageDown), Mod.ControlModifier) == "ctrl+page down"
    assert chord_from_key(int(Qt.Key.Key_Semicolon), Mod.ControlModifier) == "ctrl+;"
    assert chord_from_key(int(Qt.Key.Key_5), Mod.ControlModifier | Mod.KeypadModifier) == "ctrl+num5"


def test_chord_from_key_refuses_modifiers_alone_and_unknown_keys() -> None:
    assert chord_from_key(int(Qt.Key.Key_Control), Mod.ControlModifier) is None
    assert chord_from_key(int(Qt.Key.Key_Alt), Mod.AltModifier | Mod.ControlModifier) is None
    assert chord_from_key(int(Qt.Key.Key_VolumeUp), Mod.ControlModifier) is None


@pytest.mark.skipif(sys.platform != "win32", reason="the virtual key is only read on Windows")
def test_the_virtual_key_decides_on_windows() -> None:
    # Shift+1 arrives as Key_Exclam on a US layout; RegisterHotKey needs VK "1" (0x31).
    assert chord_from_key(int(Qt.Key.Key_Exclam), Mod.ControlModifier | Mod.ShiftModifier, 0x31) == "ctrl+shift+1"
    assert chord_from_key(int(Qt.Key.Key_5), Mod.ControlModifier, 0x65) == "ctrl+num5"  # VK_NUMPAD5
    assert chord_from_key(int(Qt.Key.Key_Semicolon), Mod.AltModifier, 0xBA) == "alt+;"  # VK_OEM_1


def test_modifiers_display() -> None:
    assert modifiers_display(Mod.ControlModifier | Mod.AltModifier) == "Ctrl+Alt+"
    assert modifiers_display(Mod.MetaModifier) == "Win+"
    assert modifiers_display(Mod.NoModifier) == ""


def test_every_capturable_key_is_a_core_key_name() -> None:
    names = set(chords._QT_NAMED.values()) | set(chords._VK_NAMED.values())
    assert names <= chords.KEY_NAMES


def test_mirror_matches_the_core_module_when_present() -> None:
    """After the merge the core module exists: the UI's mirror must agree with it exactly."""
    if importlib.util.find_spec("app.companion.chords") is None:
        pytest.skip("the core chords module is not in this checkout")
    core = importlib.import_module("app.companion.chords")
    assert core.KEY_NAMES == chords.KEY_NAMES
    for sample in SAMPLES:
        core_chord = core.parse_chord(sample)
        mine = parse_chord(sample)
        assert (core_chord is None) == (mine is None), sample
        assert core.normalize_chord(sample) == normalize_chord(sample), sample
        assert core.display_chord(sample) == display_chord(sample), sample
