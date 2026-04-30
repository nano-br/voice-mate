from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol


class InputListener(Protocol):
    """Interface para diferentes métodos de input (teclado, mouse, etc.)."""

    def listen(self, on_toggle: Callable[[], None]) -> None:
        """Bloqueia e chama on_toggle a cada evento de trigger."""
        ...

    def reinstall(self) -> None:
        """Reinstala o hook subjacente sem alterar o callback registrado.

        Necessário porque o Windows remove silenciosamente hooks de baixo
        nível (WH_KEYBOARD_LL / WH_MOUSE_LL) que excedam LowLevelHooksTimeout
        sob carga, sem notificar a aplicação.
        """
        ...

    def stop(self) -> None:
        """Para de escutar e libera recursos."""
        ...


class KeyboardHotkeyListener:
    """Escuta um hotkey global via biblioteca keyboard."""

    def __init__(self, hotkey: str) -> None:
        self._hotkey = hotkey
        self._on_toggle: Callable[[], None] | None = None
        self._lock = threading.Lock()

    def listen(self, on_toggle: Callable[[], None]) -> None:
        import keyboard

        with self._lock:
            self._on_toggle = on_toggle
            keyboard.add_hotkey(self._hotkey, on_toggle)
        keyboard.wait()

    def reinstall(self) -> None:
        import keyboard

        with self._lock:
            if self._on_toggle is None:
                return
            keyboard.unhook_all()
            keyboard.add_hotkey(self._hotkey, self._on_toggle)

    def stop(self) -> None:
        import keyboard

        keyboard.unhook_all()


class MouseButtonListener:
    """Escuta cliques de botões do mouse (inclusive botões laterais)."""

    def __init__(self, button: str = "x") -> None:
        self._button = button
        self._on_toggle: Callable[[], None] | None = None
        self._lock = threading.Lock()

    def listen(self, on_toggle: Callable[[], None]) -> None:
        import mouse

        with self._lock:
            self._on_toggle = on_toggle
            mouse.on_button(on_toggle, buttons=(self._button,), types=("up",))
        mouse.wait()

    def reinstall(self) -> None:
        import mouse

        with self._lock:
            if self._on_toggle is None:
                return
            mouse.unhook_all()
            mouse.on_button(self._on_toggle, buttons=(self._button,), types=("up",))

    def stop(self) -> None:
        import mouse

        mouse.unhook_all()


def create_listener(input_method: str, hotkey: str, mouse_button: str) -> InputListener:
    """Factory que retorna o listener correto baseado na configuração."""
    if input_method == "mouse":
        return MouseButtonListener(button=mouse_button)  # type: ignore[return-value]
    return KeyboardHotkeyListener(hotkey=hotkey)  # type: ignore[return-value]
