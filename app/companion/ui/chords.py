"""Hotkey chords in the UI: key capture and QKeySequence conversion ("ctrl+alt+v" <-> Qt).

The chord text format, its rules and its display belong to the core module
`app/companion/chords.py` and are imported (and re-exported) from it. This module
only adds what needs Qt: turning a key press into a chord, and chords into
QKeySequence objects and back.
"""

from __future__ import annotations

import sys
from typing import Final

from PySide6.QtCore import QKeyCombination, Qt
from PySide6.QtGui import QKeySequence

from app.companion.chords import (  # noqa: F401 (re-exported for the UI modules)
    _DISPLAY_MODIFIERS,
    _KEY_ALIASES,
    _MODIFIER_ALIASES,
    FUNCTION_KEYS,
    KEY_NAMES,
    MODIFIERS,
    NUMPAD_KEYS,
    Chord,
    Modifier,
    display_chord,
    normalize_chord,
    parse_chord,
)

# ---------------------------------------------------------------------------------------
# Qt layer: key capture and QKeySequence conversion.
# ---------------------------------------------------------------------------------------

_MODIFIER_FLAGS: Final[dict[Modifier, Qt.KeyboardModifier]] = {
    "ctrl": Qt.KeyboardModifier.ControlModifier,
    "alt": Qt.KeyboardModifier.AltModifier,
    "shift": Qt.KeyboardModifier.ShiftModifier,
    "win": Qt.KeyboardModifier.MetaModifier,
}
_QT_NAMED: Final[dict[Qt.Key, str]] = {
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Backspace: "backspace",
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
    Qt.Key.Key_Semicolon: ";",
    Qt.Key.Key_Equal: "=",
    Qt.Key.Key_Comma: ",",
    Qt.Key.Key_Minus: "-",
    Qt.Key.Key_Period: ".",
    Qt.Key.Key_Slash: "/",
    Qt.Key.Key_QuoteLeft: "`",
    Qt.Key.Key_BracketLeft: "[",
    Qt.Key.Key_Backslash: "\\",
    Qt.Key.Key_BracketRight: "]",
    Qt.Key.Key_Apostrophe: "'",
}
_QT_KEYS_BY_NAME: Final[dict[str, Qt.Key]] = {name: key for key, name in _QT_NAMED.items() if key != Qt.Key.Key_Enter}
# Windows virtual keys: what RegisterHotKey uses. With Shift held Qt reports the shifted
# symbol (Shift+1 -> "!"), and numpad keys share Qt keys with the top row, so on Windows
# the virtual key decides.
_VK_NAMED: Final[dict[int, str]] = {
    0x20: "space",
    0x0D: "enter",
    0x09: "tab",
    0x1B: "esc",
    0x08: "backspace",
    0x2D: "insert",
    0x2E: "delete",
    0x24: "home",
    0x23: "end",
    0x21: "page up",
    0x22: "page down",
    0x26: "up",
    0x28: "down",
    0x25: "left",
    0x27: "right",
    0x13: "pause",
    0x2C: "print screen",
    0xBA: ";",
    0xBB: "=",
    0xBC: ",",
    0xBD: "-",
    0xBE: ".",
    0xBF: "/",
    0xC0: "`",
    0xDB: "[",
    0xDC: "\\",
    0xDD: "]",
    0xDE: "'",
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


def _vk_name(vk: int) -> str | None:
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x60 <= vk <= 0x69:
        return f"num{vk - 0x60}"
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x70 + 1}"
    return _VK_NAMED.get(vk)


def key_name_for(key: int, modifiers: Qt.KeyboardModifier, native_virtual_key: int = 0) -> str | None:
    """The chord name of one key press (None for modifiers and keys a chord cannot use)."""
    if sys.platform == "win32" and native_virtual_key:
        return _vk_name(native_virtual_key)
    if 0x30 <= key <= 0x39:
        digit = chr(key)
        return f"num{digit}" if modifiers & Qt.KeyboardModifier.KeypadModifier else digit
    if 0x41 <= key <= 0x5A:
        return chr(key).lower()
    if _F1 <= key <= _F24:
        return f"f{key - _F1 + 1}"
    try:
        qt_key = Qt.Key(key)
    except ValueError:
        return None
    return _QT_NAMED.get(qt_key)


def is_modifier_key(key: int) -> bool:
    try:
        return Qt.Key(key) in _MODIFIER_KEYS
    except ValueError:
        return False


def modifier_names(modifiers: Qt.KeyboardModifier) -> list[Modifier]:
    return [name for name in MODIFIERS if modifiers & _MODIFIER_FLAGS[name]]


def chord_from_key(key: int, modifiers: Qt.KeyboardModifier, native_virtual_key: int = 0) -> str | None:
    """A key press as chord text ("ctrl+alt+v"). None for a bare modifier or a key no
    chord can use; whether the chord is ALLOWED is `parse_chord`'s (and check_hotkey's) call."""
    if is_modifier_key(key):
        return None
    name = key_name_for(key, modifiers, native_virtual_key)
    if name is None:
        return None
    return "+".join([*modifier_names(modifiers), name])


def _split(text: str) -> tuple[list[Modifier], str]:
    """Lenient split (no validity rules): for converting any stored text to Qt."""
    modifiers: set[Modifier] = set()
    key = ""
    for part in (" ".join(p.split()) for p in text.strip().lower().split("+")):
        mod = _MODIFIER_ALIASES.get(part)
        if mod is not None:
            modifiers.add(mod)
        elif part:
            key = _KEY_ALIASES.get(part, part)
    return [mod for mod in MODIFIERS if mod in modifiers], key


def chord_to_sequence(chord: str) -> QKeySequence:
    """ "ctrl+alt+v" -> QKeySequence(Ctrl+Alt+V); empty when the chord has no known key."""
    modifiers, key_name = _split(chord)
    flags = Qt.KeyboardModifier.NoModifier
    for name in modifiers:
        flags |= _MODIFIER_FLAGS[name]
    if key_name in NUMPAD_KEYS:
        flags |= Qt.KeyboardModifier.KeypadModifier
        key: Qt.Key | None = Qt.Key(ord(key_name[3]))
    elif len(key_name) == 1 and key_name.isascii() and key_name.isalnum():
        key = Qt.Key(ord(key_name.upper()))
    elif key_name in FUNCTION_KEYS:
        key = Qt.Key(_F1 + int(key_name[1:]) - 1)
    else:
        key = _QT_KEYS_BY_NAME.get(key_name)
    if key is None:
        return QKeySequence()
    return QKeySequence(QKeyCombination(flags, key))


def sequence_to_chord(sequence: QKeySequence) -> str:
    """The first combination of `sequence` as chord text; "" when empty or not representable."""
    if sequence.isEmpty():
        return ""
    combination = sequence[0]  # type: ignore[index]  # QKeySequence::operator[], missing from the stubs
    return chord_from_key(int(combination.key()), combination.keyboardModifiers()) or ""


def modifiers_display(modifiers: Qt.KeyboardModifier) -> str:
    """Live preview while capturing: "Ctrl+Alt+" (empty when no modifier is held)."""
    names = [_DISPLAY_MODIFIERS[name] for name in modifier_names(modifiers)]
    return "+".join(names) + "+" if names else ""
