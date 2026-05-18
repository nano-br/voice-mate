from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from app.services.multi_hotkey_listener import MultiHotkeyListener


class FakeKeyboardModule:
    def __init__(self) -> None:
        self.hotkeys: list[tuple[str, Callable[[], None]]] = []
        self.unhook_all_calls = 0
        self.wait_called = False

    def add_hotkey(self, hotkey: str, callback: Callable[[], None]) -> None:
        self.hotkeys.append((hotkey, callback))

    def unhook_all(self) -> None:
        self.unhook_all_calls += 1
        self.hotkeys.clear()

    def wait(self) -> None:
        self.wait_called = True


@pytest.fixture
def fake_keyboard(monkeypatch: pytest.MonkeyPatch) -> FakeKeyboardModule:
    fake = FakeKeyboardModule()
    monkeypatch.setitem(sys.modules, "keyboard", fake)
    return fake


def _cb_a() -> None:
    return None


def _cb_b() -> None:
    return None


def test_listen_registers_all_bindings(fake_keyboard: FakeKeyboardModule) -> None:
    listener = MultiHotkeyListener({"ctrl+alt+v": _cb_a, "ctrl+alt+a": _cb_b})
    listener.listen()
    assert ("ctrl+alt+v", _cb_a) in fake_keyboard.hotkeys
    assert ("ctrl+alt+a", _cb_b) in fake_keyboard.hotkeys
    assert fake_keyboard.wait_called


def test_reinstall_unhooks_and_re_adds_all(fake_keyboard: FakeKeyboardModule) -> None:
    listener = MultiHotkeyListener({"ctrl+alt+v": _cb_a, "ctrl+alt+a": _cb_b})
    listener.listen()

    listener.reinstall()

    assert fake_keyboard.unhook_all_calls == 1
    assert len(fake_keyboard.hotkeys) == 2
    pairs = set(fake_keyboard.hotkeys)
    assert ("ctrl+alt+v", _cb_a) in pairs
    assert ("ctrl+alt+a", _cb_b) in pairs


def test_reinstall_noop_before_listen(fake_keyboard: FakeKeyboardModule) -> None:
    listener = MultiHotkeyListener({"ctrl+alt+v": _cb_a})
    listener.reinstall()
    assert fake_keyboard.unhook_all_calls == 0
    assert fake_keyboard.hotkeys == []


def test_empty_bindings_raises() -> None:
    with pytest.raises(ValueError):
        MultiHotkeyListener({})
