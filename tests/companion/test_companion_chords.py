from __future__ import annotations

import pytest

from app.companion.chords import KEY_NAMES, display_chord, normalize_chord, parse_chord


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ctrl+alt+v", "ctrl+alt+v"),
        ("Alt+Ctrl+V", "ctrl+alt+v"),
        (" control + alt + a ", "ctrl+alt+a"),
        ("shift+ctrl+alt+F23", "ctrl+alt+shift+f23"),
        ("win+shift+s", "shift+win+s"),
        ("f9", "f9"),  # function keys need no modifier
        ("ctrl+escape", "ctrl+esc"),
        ("ctrl+alt+num5", "ctrl+alt+num5"),
        ("ctrl+;", "ctrl+;"),
        ("shift+f1", "shift+f1"),
        ("shift+page up", "shift+page up"),  # Shift alone is fine on a non-typing key
        ("ctrl+pageup", "ctrl+page up"),
        ("ctrl+alt+page  down", "ctrl+alt+page down"),
        ("ctrl+print screen", "ctrl+print screen"),
        # The settings window's capture format: ctrl+alt+shift+win, then the key.
        ("ctrl+alt+shift+win+backspace", "ctrl+alt+shift+win+backspace"),
    ],
)
def test_normalizes_valid_chords(text: str, expected: str) -> None:
    assert normalize_chord(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "v",  # no modifier
        "ctrl+alt",  # no key
        "ctrl+alt+v+b",  # two keys
        "ctrl+ctrl+v",  # duplicate modifier
        "ctrl+f12",  # reserved by Windows, even with modifiers
        "f12",
        "shift+a",  # would steal capital letters
        "shift+space",
        "ctrl+alt+unknownkey",
        "ctrl++",
        "ctrl+alt+",
    ],
)
def test_rejects_unusable_chords(text: str) -> None:
    assert parse_chord(text) is None
    assert normalize_chord(text) is None


def test_display_uses_human_names() -> None:
    assert display_chord("ctrl+alt+v") == "Ctrl+Alt+V"
    assert display_chord("alt+shift+ctrl+f23") == "Ctrl+Alt+Shift+F23"
    assert display_chord("ctrl+page up") == "Ctrl+PageUp"
    assert display_chord("ctrl+print screen") == "Ctrl+PrintScreen"
    assert display_chord("ctrl+alt+num3") == "Ctrl+Alt+Num3"
    assert display_chord("not a chord") == "not a chord"


def test_every_key_name_round_trips() -> None:
    for key in KEY_NAMES:
        if key == "f12":
            continue
        chord = parse_chord(f"ctrl+alt+{key}")
        assert chord is not None, key
        assert chord.key == key
        assert parse_chord(chord.normalized()) == chord
