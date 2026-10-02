"""Pure parts of the Linux listeners (hotkey parsing) — no real pynput/evdev."""

from __future__ import annotations

import pytest

from app.platform.listeners.evdev_listener import EvdevHotkeyListener, parse_hotkey
from app.platform.listeners.pynput_listener import PynputHotkeyListener, to_pynput_combo


def test_to_pynput_combo_converts_modifiers() -> None:
    assert to_pynput_combo("ctrl+alt+v") == "<ctrl>+<alt>+v"
    assert to_pynput_combo("ctrl+shift+r") == "<ctrl>+<shift>+r"


def test_to_pynput_combo_named_keys_and_aliases() -> None:
    assert to_pynput_combo("win+space") == "<cmd>+<space>"
    assert to_pynput_combo("f9") == "<f9>"


def test_to_pynput_combo_empty_raises() -> None:
    with pytest.raises(ValueError, match="Empty hotkey"):
        to_pynput_combo("  ")


def test_pynput_listener_requires_bindings() -> None:
    with pytest.raises(ValueError, match="requires at least one binding"):
        PynputHotkeyListener({})


def test_parse_hotkey_modifiers_and_key() -> None:
    parsed = parse_hotkey("ctrl+alt+v", lambda: None)
    assert parsed.modifiers == frozenset({"ctrl", "alt"})
    assert parsed.key_code == "KEY_V"


def test_parse_hotkey_super_alias() -> None:
    parsed = parse_hotkey("super+a", lambda: None)
    assert parsed.modifiers == frozenset({"super"})
    assert parsed.key_code == "KEY_A"


def test_parse_hotkey_unknown_modifier_raises() -> None:
    with pytest.raises(ValueError, match="Unknown modifier"):
        parse_hotkey("hyper+v", lambda: None)


def test_evdev_listener_requires_bindings() -> None:
    with pytest.raises(ValueError, match="requires at least one binding"):
        EvdevHotkeyListener({})
