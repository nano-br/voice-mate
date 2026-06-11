"""Hotkeys globais via pynput (Linux X11; também funciona em macOS/Windows).

Não funciona em Wayland puro (o compositor não entrega eventos globais a
clientes X) — lá o gatilho default é o `EvdevHotkeyListener`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

_MODIFIERS = {"ctrl", "alt", "shift", "cmd", "win", "super", "meta"}
_MODIFIER_ALIASES = {"win": "cmd", "super": "cmd", "meta": "cmd"}


def to_pynput_combo(hotkey: str) -> str:
    """Converte a sintaxe da lib `keyboard` ("ctrl+alt+v") para a do pynput ("<ctrl>+<alt>+v")."""
    parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not parts:
        raise ValueError(f"Hotkey vazio: {hotkey!r}")
    converted: list[str] = []
    for part in parts:
        if part in _MODIFIERS:
            converted.append(f"<{_MODIFIER_ALIASES.get(part, part)}>")
        elif len(part) == 1:
            converted.append(part)
        else:
            # Teclas nomeadas (f1..f24, space, esc, ...) usam <nome> no pynput.
            converted.append(f"<{part}>")
    return "+".join(converted)


class PynputHotkeyListener:
    """Registra múltiplos hotkeys globais via pynput.keyboard.GlobalHotKeys."""

    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        if not bindings:
            raise ValueError("PynputHotkeyListener exige ao menos um binding")
        self._bindings = dict(bindings)
        self._hotkeys: Any | None = None
        self._lock = threading.Lock()

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        from pynput import keyboard

        mapping = {to_pynput_combo(hk): cb for hk, cb in self._bindings.items()}
        with self._lock:
            self._hotkeys = keyboard.GlobalHotKeys(mapping)
            self._hotkeys.start()
        self._hotkeys.join()

    def reinstall(self) -> None:
        """No-op: o hook de hooks silenciosamente removidos é exclusivo do Windows."""
        return None

    def stop(self) -> None:
        with self._lock:
            if self._hotkeys is not None:
                self._hotkeys.stop()
                self._hotkeys = None
