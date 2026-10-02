"""Hotkey chords ("ctrl+alt+v"): parsing, normalization and display.

Platform-neutral on purpose: settings validation and `check_hotkey` run on every OS,
and the Windows hotkey thread maps the parsed key names to virtual-key codes.

Stored form (`normalize_chord`): lowercase parts joined by "+", modifiers first in the
order ctrl, alt, shift, win, then exactly one key, e.g. "ctrl+alt+shift+page up".
Accepted keys:
- letters "a".."z", digits "0".."9", "f1".."f24" ("f12" is reserved by Windows);
- "space", "enter", "tab", "esc", "backspace", "insert", "delete", "home", "end",
  "page up", "page down", "up", "down", "left", "right", "pause", "print screen";
- "num0".."num9" and the punctuation keys ; = , - . / ` [ \\ ] ' (US layout positions).
Input aliases: "control" -> ctrl; "windows", "super", "meta" -> win; "escape" -> esc;
"return" -> enter; "del" -> delete; "ins" -> insert; "pageup"/"pgup" -> "page up";
"pagedown"/"pgdn" -> "page down"; "printscreen"/"prtsc" -> "print screen".
Rules: a modifier is required unless the key is F1..F24, and Shift alone does not count
for keys that type a character. Display names (`display_chord`) are stable: "Ctrl",
"Alt", "Shift", "Win", upper-case letters and F-keys, "Space", "PageUp", "PageDown",
"PrintScreen", "Num5" and so on, joined by "+".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

Modifier = Literal["ctrl", "alt", "shift", "win"]
MODIFIERS: Final[tuple[Modifier, ...]] = ("ctrl", "alt", "shift", "win")
MODIFIER_ALIASES: Final[dict[str, Modifier]] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "super": "win",
    "meta": "win",
}

FUNCTION_KEYS: Final = tuple(f"f{n}" for n in range(1, 25))
# Windows reserves F12 for the debugger (RegisterHotKey refuses it, even with modifiers).
RESERVED_KEYS: Final = frozenset({"f12"})
LETTER_KEYS: Final = tuple(chr(code) for code in range(ord("a"), ord("z") + 1))
DIGIT_KEYS: Final = tuple(str(n) for n in range(10))
# Names as the settings window's capture widget writes them (and the engine's hotkey
# library reads them): lowercase, with spaces ("page up").
NAMED_KEYS: Final = (
    "space",
    "enter",
    "tab",
    "esc",
    "backspace",
    "insert",
    "delete",
    "home",
    "end",
    "page up",
    "page down",
    "up",
    "down",
    "left",
    "right",
    "pause",
    "print screen",
)
NUMPAD_KEYS: Final = tuple(f"num{n}" for n in range(10))
PUNCTUATION_KEYS: Final = (";", "=", ",", "-", ".", "/", "`", "[", "\\", "]", "'")
KEY_ALIASES: Final = {
    "escape": "esc",
    "return": "enter",
    "del": "delete",
    "ins": "insert",
    "pageup": "page up",
    "pgup": "page up",
    "pagedown": "page down",
    "pgdn": "page down",
    "printscreen": "print screen",
    "prtsc": "print screen",
}

# Every key name a chord may use (the Windows thread must map each one to a VK code).
KEY_NAMES: Final = frozenset(FUNCTION_KEYS + LETTER_KEYS + DIGIT_KEYS + NAMED_KEYS + NUMPAD_KEYS + PUNCTUATION_KEYS)
# Keys that type a character: Shift alone on them would steal normal typing.
_PRINTABLE_KEYS: Final = frozenset(LETTER_KEYS + DIGIT_KEYS + PUNCTUATION_KEYS + ("space",))

DISPLAY_MODIFIERS: Final[dict[Modifier, str]] = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
_DISPLAY_NAMED: Final = {
    "space": "Space",
    "enter": "Enter",
    "tab": "Tab",
    "esc": "Esc",
    "backspace": "Backspace",
    "insert": "Insert",
    "delete": "Delete",
    "home": "Home",
    "end": "End",
    "page up": "PageUp",
    "page down": "PageDown",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "pause": "Pause",
    "print screen": "PrintScreen",
}


@dataclass(frozen=True)
class Chord:
    modifiers: frozenset[Modifier]
    key: str

    def normalized(self) -> str:
        """Canonical text form: modifiers in a fixed order, then the key ("ctrl+alt+v")."""
        parts: list[str] = [mod for mod in MODIFIERS if mod in self.modifiers]
        parts.append(self.key)
        return "+".join(parts)


def parse_chord(text: str) -> Chord | None:
    """Parse "Ctrl+Alt+V" style text. None when it is not a usable global hotkey.

    Rules: exactly one non-modifier key from `KEY_NAMES`; at least one modifier unless
    the key is F1..F24; Shift alone does not count for keys that type a character;
    F12 is reserved by Windows.
    """
    raw = text.strip().lower()
    if not raw:
        return None
    # "+" separates parts, so a trailing "+" (the plus key) is not supported. Inner runs of
    # spaces collapse ("page  up" -> "page up").
    parts = [" ".join(part.split()) for part in raw.split("+")]
    if any(not part for part in parts):
        return None
    modifiers: set[Modifier] = set()
    key: str | None = None
    for part in parts:
        mod = MODIFIER_ALIASES.get(part)
        if mod is not None:
            if mod in modifiers:
                return None
            modifiers.add(mod)
            continue
        if key is not None:
            return None
        key = KEY_ALIASES.get(part, part)
    if key is None or key not in KEY_NAMES or key in RESERVED_KEYS:
        return None
    if key not in FUNCTION_KEYS:
        if not modifiers:
            return None
        if modifiers == {"shift"} and key in _PRINTABLE_KEYS:
            return None
    return Chord(frozenset(modifiers), key)


def normalize_chord(text: str) -> str | None:
    chord = parse_chord(text)
    return chord.normalized() if chord is not None else None


def display_chord(text: str) -> str:
    """Human form for menus and tooltips ("Ctrl+Alt+V"); unparsable text is returned as is."""
    chord = parse_chord(text)
    if chord is None:
        return text
    parts = [DISPLAY_MODIFIERS[mod] for mod in MODIFIERS if mod in chord.modifiers]
    key = chord.key
    if key in _DISPLAY_NAMED:
        parts.append(_DISPLAY_NAMED[key])
    elif key.startswith("num") and len(key) == 4:
        parts.append(f"Num{key[3]}")
    else:
        parts.append(key.upper())
    return "+".join(parts)
