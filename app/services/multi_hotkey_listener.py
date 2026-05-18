from __future__ import annotations

import threading
from collections.abc import Callable


class MultiHotkeyListener:
    """Listener que registra múltiplos hotkeys globais com callbacks distintos.

    Compatível estruturalmente com `InputListener` Protocol (listen / reinstall
    / stop), permitindo reusar `ListenerKeepalive` sem mudanças.
    """

    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        if not bindings:
            raise ValueError("MultiHotkeyListener exige ao menos um binding")
        self._bindings: dict[str, Callable[[], None]] = dict(bindings)
        self._lock = threading.Lock()
        self._installed = False

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        import keyboard

        with self._lock:
            for hotkey, callback in self._bindings.items():
                keyboard.add_hotkey(hotkey, callback)
            self._installed = True
        keyboard.wait()

    def reinstall(self) -> None:
        import keyboard

        with self._lock:
            if not self._installed:
                return
            keyboard.unhook_all()
            for hotkey, callback in self._bindings.items():
                keyboard.add_hotkey(hotkey, callback)

    def stop(self) -> None:
        import keyboard

        keyboard.unhook_all()
