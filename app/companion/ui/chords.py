"""Hotkey chords: the stored format ("ctrl+alt+v", the engine's `keyboard` syntax) <-> Qt.

One chord is optional modifiers (always in the order ctrl, alt, shift, win) plus
one key. Key names follow the `keyboard` library: letters, digits, "f1".."f24",
"space", "enter", "page up"... Only keys that map to a fixed virtual key on every
layout are offered (no punctuation, no keypad digits), so the chord means the same
thing to RegisterHotKey, the engine and the user.
"""

from __future__ import annotations

import sys
from typing import Final

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeySequence

MODIFIER_ORDER: Final = ("ctrl", "alt", "shift", "win")

_MODIFIER_FLAGS: Final[dict[str, Qt.KeyboardModifier]] = {
    "ctrl": Qt.KeyboardModifier.ControlModifier,
    "alt": Qt.KeyboardModifier.AltModifier,
    "shift": Qt.KeyboardModifier.ShiftModifier,
    "win": Qt.KeyboardModifier.MetaModifier,
}
_MODIFIER_ALIASES: Final[dict[str, str]] = {
    "control": "ctrl",
    "windows": "win",
    "super": "win",
    "meta": "win",
    "cmd": "win",
    "command": "win",
}

_NAMED_KEYS: Final[dict[Qt.Key, str]] = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page up",
    Qt.Key.Key_PageDown: "page down",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Pause: "pause",
    Qt.Key.Key_Print: "print screen",
}
_KEYS_BY_NAME: Final[dict[str, Qt.Key]] = {name: key for key, name in _NAMED_KEYS.items() if key != Qt.Key.Key_Enter}
_KEY_ALIASES: Final[dict[str, str]] = {
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "ins": "insert",
    "pageup": "page up",
    "pgup": "page up",
    "page_up": "page up",
    "pagedown": "page down",
    "pgdn": "page down",
    "page_down": "page down",
    "print": "print screen",
    "print_screen": "print screen",
    "prtsc": "print screen",
}
# Key caps as printed on keyboards: deliberately not translated (Windows shows
# "Ctrl+Alt+V" in every language).
_KEY_DISPLAY: Final[dict[str, str]] = {
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "backspace": "Backspace",
    "esc": "Esc",
    "insert": "Insert",
    "delete": "Delete",
    "home": "Home",
    "end": "End",
    "page up": "Page Up",
    "page down": "Page Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "pause": "Pause",
    "print screen": "Print Screen",
}
_MODIFIER_DISPLAY: Final[dict[str, str]] = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win" if sys.platform == "win32" else "Super",
}

_F1: Final = int(Qt.Key.Key_F1)
_F24: Final = int(Qt.Key.Key_F24)
_MODIFIER_KEYS: Final = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Alt,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Meta,
    Qt.Key.Key_Super_L,
    Qt.Key.Key_Super_R,
    Qt.Key.Key_Hyper_L,
    Qt.Key.Key_Hyper_R,
    Qt.Key.Key_CapsLock,
    Qt.Key.Key_NumLock,
    Qt.Key.Key_ScrollLock,
}


def split_chord(chord: str) -> tuple[list[str], str]:
    """("ctrl+Alt+PgUp") -> (["ctrl", "alt"], "page up"): canonical modifiers in order, and the key."""
    parts = [part.strip().lower() for part in chord.split("+")]
    parts = [part for part in parts if part]
    modifiers: set[str] = set()
    key = ""
    for part in parts:
        name = _MODIFIER_ALIASES.get(part, part)
        if name in _MODIFIER_FLAGS:
            modifiers.add(name)
        else:
            key = _KEY_ALIASES.get(part, part)
    return [name for name in MODIFIER_ORDER if name in modifiers], key


def normalize_chord(chord: str) -> str:
    """Canonical spelling: lower case, aliases resolved, modifiers in the fixed order."""
    modifiers, key = split_chord(chord)
    return "+".join([*modifiers, key] if key else modifiers)


def is_function_key(key_name: str) -> bool:
    return len(key_name) >= 2 and key_name[0] == "f" and key_name[1:].isdigit() and 1 <= int(key_name[1:]) <= 24


def _qt_key(key_name: str) -> Qt.Key | None:
    if len(key_name) == 1 and (key_name.isascii() and key_name.isalnum()):
        return Qt.Key(ord(key_name.upper()))
    if is_function_key(key_name):
        return Qt.Key(_F1 + int(key_name[1:]) - 1)
    return _KEYS_BY_NAME.get(key_name)


def chord_to_sequence(chord: str) -> QKeySequence:
    """ "ctrl+alt+v" -> QKeySequence(Ctrl+Alt+V); an empty sequence when the chord is empty or unknown."""
    modifiers, key_name = split_chord(chord)
    key = _qt_key(key_name)
    if key is None:
        return QKeySequence()
    flags = Qt.KeyboardModifier.NoModifier
    for name in modifiers:
        flags |= _MODIFIER_FLAGS[name]
    return QKeySequence(QKeyCombination(flags, key))


def sequence_to_chord(sequence: QKeySequence) -> str:
    """The first combination of `sequence` as a chord; "" when empty or not representable."""
    if sequence.isEmpty():
        return ""
    combination = sequence[0]  # type: ignore[index]  # QKeySequence::operator[], missing from the stubs
    return chord_from_key(int(combination.key()), combination.keyboardModifiers()) or ""


def key_name_for(key: int, native_virtual_key: int = 0) -> str | None:
    """The chord name of one Qt key (None for modifiers and unsupported keys).

    On Windows, letters and digits come from the virtual key: with Shift held, Qt
    reports the shifted symbol (Shift+1 -> "!"), while RegisterHotKey wants VK "1".
    """
    if sys.platform == "win32" and (0x30 <= native_virtual_key <= 0x39 or 0x41 <= native_virtual_key <= 0x5A):
        return chr(native_virtual_key).lower()
    if 0x30 <= key <= 0x39 or 0x41 <= key <= 0x5A:
        return chr(key).lower()
    if _F1 <= key <= _F24:
        return f"f{key - _F1 + 1}"
    try:
        qt_key = Qt.Key(key)
    except ValueError:
        return None
    return _NAMED_KEYS.get(qt_key)


def is_modifier_key(key: int) -> bool:
    try:
        return Qt.Key(key) in _MODIFIER_KEYS
    except ValueError:
        return False


def modifier_names(modifiers: Qt.KeyboardModifier) -> list[str]:
    return [name for name in MODIFIER_ORDER if modifiers & _MODIFIER_FLAGS[name]]


def chord_from_key(key: int, modifiers: Qt.KeyboardModifier, native_virtual_key: int = 0) -> str | None:
    """A key press as a chord ("ctrl+alt+v"); None for a bare modifier or an unsupported key.

    Keypad digits are refused: they are different virtual keys from the top-row digits.
    """
    if is_modifier_key(key):
        return None
    if modifiers & Qt.KeyboardModifier.KeypadModifier and (0x30 <= key <= 0x39):
        return None
    name = key_name_for(key, native_virtual_key)
    if name is None:
        return None
    return "+".join([*modifier_names(modifiers), name])


def chord_display(chord: str) -> str:
    """ "ctrl+alt+page up" -> "Ctrl+Alt+Page Up" (key caps, not translated); "" stays ""."""
    modifiers, key = split_chord(chord)
    if not key and not modifiers:
        return ""
    names = [_MODIFIER_DISPLAY[name] for name in modifiers]
    if key:
        names.append(_KEY_DISPLAY.get(key, key.upper() if len(key) <= 3 else key.title()))
    return "+".join(names)


def modifiers_display(modifiers: Qt.KeyboardModifier) -> str:
    """Live preview while capturing: "Ctrl+Alt+" (empty when no modifier is held)."""
    names = [_MODIFIER_DISPLAY[name] for name in modifier_names(modifiers)]
    return "+".join(names) + "+" if names else ""
