from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from app.core.input_listener import (
    KeyboardHotkeyListener,
    MouseButtonListener,
    create_listener,
)


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


class FakeMouseModule:
    def __init__(self) -> None:
        self.handlers: list[tuple[Callable[[], None], tuple[str, ...], tuple[str, ...]]] = []
        self.unhook_all_calls = 0

    def on_button(
        self,
        callback: Callable[[], None],
        buttons: tuple[str, ...],
        types: tuple[str, ...],
    ) -> None:
        self.handlers.append((callback, buttons, types))

    def unhook_all(self) -> None:
        self.unhook_all_calls += 1
        self.handlers.clear()

    def wait(self) -> None:
        pass


@pytest.fixture
def fake_keyboard(monkeypatch: pytest.MonkeyPatch) -> FakeKeyboardModule:
    fake = FakeKeyboardModule()
    monkeypatch.setitem(sys.modules, "keyboard", fake)
    return fake


@pytest.fixture
def fake_mouse(monkeypatch: pytest.MonkeyPatch) -> FakeMouseModule:
    fake = FakeMouseModule()
    monkeypatch.setitem(sys.modules, "mouse", fake)
    return fake


def _noop() -> None:
    return None


def test_keyboard_listener_listen_registers_hotkey(fake_keyboard: FakeKeyboardModule) -> None:
    listener = KeyboardHotkeyListener(hotkey="ctrl+alt+v", on_toggle=_noop)
    listener.listen()
    assert fake_keyboard.hotkeys == [("ctrl+alt+v", _noop)]
    assert fake_keyboard.wait_called


def test_keyboard_listener_accepts_callback_via_listen_for_back_compat(
    fake_keyboard: FakeKeyboardModule,
) -> None:
    listener = KeyboardHotkeyListener(hotkey="ctrl+alt+v")
    listener.listen(_noop)
    assert fake_keyboard.hotkeys == [("ctrl+alt+v", _noop)]


def test_keyboard_listener_reinstall_unhooks_and_re_adds(fake_keyboard: FakeKeyboardModule) -> None:
    listener = KeyboardHotkeyListener(hotkey="ctrl+alt+v", on_toggle=_noop)
    listener.listen()

    listener.reinstall()

    assert fake_keyboard.unhook_all_calls == 1
    assert fake_keyboard.hotkeys == [("ctrl+alt+v", _noop)]


def test_keyboard_listener_reinstall_noop_before_listen(fake_keyboard: FakeKeyboardModule) -> None:
    listener = KeyboardHotkeyListener(hotkey="ctrl+alt+v", on_toggle=_noop)
    listener.reinstall()
    assert fake_keyboard.unhook_all_calls == 0
    assert fake_keyboard.hotkeys == []


def test_keyboard_listener_without_callback_raises(fake_keyboard: FakeKeyboardModule) -> None:
    listener = KeyboardHotkeyListener(hotkey="ctrl+alt+v")
    with pytest.raises(RuntimeError):
        listener.listen()


def test_mouse_listener_listen_registers_button(fake_mouse: FakeMouseModule) -> None:
    listener = MouseButtonListener(button="x", on_toggle=_noop)
    listener.listen()
    assert len(fake_mouse.handlers) == 1
    callback, buttons, types_ = fake_mouse.handlers[0]
    assert callback is _noop
    assert buttons == ("x",)
    assert types_ == ("up",)


def test_mouse_listener_reinstall_unhooks_and_re_adds(fake_mouse: FakeMouseModule) -> None:
    listener = MouseButtonListener(button="x", on_toggle=_noop)
    listener.listen()

    listener.reinstall()

    assert fake_mouse.unhook_all_calls == 1
    assert len(fake_mouse.handlers) == 1
    assert fake_mouse.handlers[0][0] is _noop


def test_mouse_listener_reinstall_noop_before_listen(fake_mouse: FakeMouseModule) -> None:
    listener = MouseButtonListener(button="x", on_toggle=_noop)
    listener.reinstall()
    assert fake_mouse.unhook_all_calls == 0
    assert fake_mouse.handlers == []


def test_create_listener_dispatches_by_input_method() -> None:
    assert isinstance(create_listener("keyboard", "ctrl+alt+v", "x"), KeyboardHotkeyListener)
    assert isinstance(create_listener("mouse", "ctrl+alt+v", "x"), MouseButtonListener)
