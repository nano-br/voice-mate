"""Global hotkeys via evdev (/dev/input) — works on X11 AND Wayland.

Reads keyboard events straight from the kernel, before the display server, so
the Wayland compositor can't hide them. Requires the user to be in the `input`
group (no sudo):

    sudo usermod -aG input $USER && newgrp input

Does not work on WSL2 (there's no /dev/input mapped to the Windows keyboard) —
there the trigger is the SocketTriggerListener + Windows-side script.
"""

from __future__ import annotations

import selectors
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_GROUP_HINT = "sudo usermod -aG input $USER  # then log back in (or run `newgrp input`)"

# Logical modifier → evdev keycodes that satisfy it.
_MODIFIER_CODES = {
    "ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "win": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "super": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "cmd": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "meta": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
}


@dataclass(frozen=True)
class _ParsedHotkey:
    modifiers: frozenset[str]  # logical names: "ctrl", "alt", ...
    key_code: str  # e.g. "KEY_V"
    callback: Callable[[], None]


def parse_hotkey(hotkey: str, callback: Callable[[], None]) -> _ParsedHotkey:
    parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not parts:
        raise ValueError(f"Empty hotkey: {hotkey!r}")
    modifiers = frozenset(part for part in parts[:-1] if part in _MODIFIER_CODES)
    if len(modifiers) != len(parts) - 1:
        unknown = [p for p in parts[:-1] if p not in _MODIFIER_CODES]
        raise ValueError(f"Unknown modifier in {hotkey!r}: {unknown}")
    return _ParsedHotkey(modifiers=modifiers, key_code=f"KEY_{parts[-1].upper()}", callback=callback)


class EvdevHotkeyListener:
    """Listens for keyboard chords across all keyboards in /dev/input."""

    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        if not bindings:
            raise ValueError("EvdevHotkeyListener requires at least one binding")
        self._hotkeys = [parse_hotkey(hk, cb) for hk, cb in bindings.items()]
        self._stop_event = threading.Event()

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        try:
            import evdev
        except ImportError as exc:
            raise RuntimeError(
                "'evdev' package not installed (required for hotkeys on Wayland). "
                "Install with: poetry install --extras linux"
            ) from exc

        keyboards = self._open_keyboards(evdev)
        if not keyboards:
            raise RuntimeError(
                "No readable keyboard in /dev/input. Add your user to the input group:\n  " + _GROUP_HINT
            )

        selector = selectors.DefaultSelector()
        for device in keyboards:
            selector.register(device, selectors.EVENT_READ)
        pressed: set[str] = set()
        key_event_type = evdev.ecodes.EV_KEY
        try:
            while not self._stop_event.is_set():
                for selector_key, _mask in selector.select(timeout=0.5):
                    device = selector_key.fileobj
                    for event in device.read():  # type: ignore[union-attr]
                        if event.type != key_event_type:
                            continue
                        self._handle_key_event(evdev, event, pressed)
        finally:
            selector.close()
            for device in keyboards:
                try:
                    device.close()
                except OSError:
                    pass

    def reinstall(self) -> None:
        """No-op: hooks dropped under load are a Windows-only problem."""
        return None

    def stop(self) -> None:
        self._stop_event.set()

    # ─── internals ───────────────────────────────────────────────────────────

    @staticmethod
    def _open_keyboards(evdev: Any) -> list[Any]:  # noqa: ANN401 — module imported at runtime
        keyboards: list[Any] = []
        permission_errors = 0
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
            except PermissionError:
                permission_errors += 1
                continue
            except OSError:
                continue
            capabilities = device.capabilities()
            key_codes = set(capabilities.get(evdev.ecodes.EV_KEY, ()))
            # "Is a keyboard" heuristic: has letters (KEY_A) and modifiers.
            if evdev.ecodes.KEY_A in key_codes and evdev.ecodes.KEY_LEFTCTRL in key_codes:
                keyboards.append(device)
            else:
                device.close()
        if not keyboards and permission_errors:
            raise RuntimeError(
                f"No read permission on {permission_errors} device(s) in /dev/input. "
                "Add your user to the input group:\n  " + _GROUP_HINT
            )
        return keyboards

    def _handle_key_event(self, evdev: Any, event: Any, pressed: set[str]) -> None:  # noqa: ANN401
        key_event = evdev.categorize(event)
        code = key_event.keycode if isinstance(key_event.keycode, str) else key_event.keycode[0]
        if key_event.keystate == key_event.key_up:
            pressed.discard(code)
            return
        if key_event.keystate != key_event.key_down:
            return  # ignore key_hold (auto-repeat)
        pressed.add(code)
        for hotkey in self._hotkeys:
            if code != hotkey.key_code:
                continue
            if all(any(mod_code in pressed for mod_code in _MODIFIER_CODES[mod]) for mod in hotkey.modifiers):
                hotkey.callback()
