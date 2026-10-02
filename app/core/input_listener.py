from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol


class InputListener(Protocol):
    """Interface for different input methods (keyboard, mouse, etc.).

    The toggle callback is registered in the constructor; `listen()` only
    installs the underlying hook and blocks.
    """

    def listen(self) -> None:
        """Block and fire the registered callback on every trigger event."""
        ...

    def reinstall(self) -> None:
        """Reinstall the underlying hook without changing the registered callback.

        Needed because Windows silently removes low-level hooks
        (WH_KEYBOARD_LL / WH_MOUSE_LL) that exceed LowLevelHooksTimeout
        under load, without notifying the application.
        """
        ...

    def stop(self) -> None:
        """Stop listening and release resources."""
        ...


class KeyboardHotkeyListener:
    """Listen for a global hotkey via the keyboard library."""

    def __init__(self, hotkey: str, on_toggle: Callable[[], None] | None = None) -> None:
        self._hotkey = hotkey
        self._on_toggle: Callable[[], None] | None = on_toggle
        self._lock = threading.Lock()
        self._installed = False

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        import keyboard

        with self._lock:
            if on_toggle is not None:
                self._on_toggle = on_toggle
            if self._on_toggle is None:
                raise RuntimeError("No callback registered for the hotkey")
            keyboard.add_hotkey(self._hotkey, self._on_toggle)
            self._installed = True
        keyboard.wait()

    def reinstall(self) -> None:
        import keyboard

        with self._lock:
            if not self._installed or self._on_toggle is None:
                return
            keyboard.unhook_all()
            keyboard.add_hotkey(self._hotkey, self._on_toggle)

    def stop(self) -> None:
        import keyboard

        keyboard.unhook_all()


class MouseButtonListener:
    """Listen for mouse button clicks (including side buttons)."""

    def __init__(self, button: str = "x", on_toggle: Callable[[], None] | None = None) -> None:
        self._button = button
        self._on_toggle: Callable[[], None] | None = on_toggle
        self._lock = threading.Lock()
        self._installed = False

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        import mouse

        with self._lock:
            if on_toggle is not None:
                self._on_toggle = on_toggle
            if self._on_toggle is None:
                raise RuntimeError("No callback registered for the mouse button")
            mouse.on_button(self._on_toggle, buttons=(self._button,), types=("up",))
            self._installed = True
        mouse.wait()

    def reinstall(self) -> None:
        import mouse

        with self._lock:
            if not self._installed or self._on_toggle is None:
                return
            mouse.unhook_all()
            mouse.on_button(self._on_toggle, buttons=(self._button,), types=("up",))

    def stop(self) -> None:
        import mouse

        mouse.unhook_all()


def create_listener(input_method: str, hotkey: str, mouse_button: str) -> InputListener:
    """Factory that returns the correct listener based on the configuration."""
    if input_method == "mouse":
        return MouseButtonListener(button=mouse_button)  # type: ignore[return-value]
    return KeyboardHotkeyListener(hotkey=hotkey)  # type: ignore[return-value]
