from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class InputListener(Protocol):
    """Interface para diferentes métodos de input (teclado, mouse, etc.)."""

    def listen(self, on_toggle: Callable[[], None]) -> None:
        """Bloqueia e chama on_toggle a cada evento de trigger."""
        ...

    def stop(self) -> None:
        """Para de escutar e libera recursos."""
        ...


class KeyboardHotkeyListener:
    """Escuta um hotkey global via biblioteca keyboard."""

    def __init__(self, hotkey: str) -> None:
        self._hotkey = hotkey

    def listen(self, on_toggle: Callable[[], None]) -> None:
        import keyboard

        keyboard.add_hotkey(self._hotkey, on_toggle)
        keyboard.wait()

    def stop(self) -> None:
        import keyboard

        keyboard.unhook_all()


class MouseButtonListener:
    """Escuta cliques de botões do mouse (inclusive botões laterais)."""

    def __init__(self, button: str = "x") -> None:
        self._button = button

    def listen(self, on_toggle: Callable[[], None]) -> None:
        import mouse

        mouse.on_button(on_toggle, buttons=(self._button,), types=("up",))
        mouse.wait()

    def stop(self) -> None:
        import mouse

        mouse.unhook_all()


def create_listener(input_method: str, hotkey: str, mouse_button: str) -> InputListener:
    """Factory que retorna o listener correto baseado na configuração."""
    if input_method == "mouse":
        return MouseButtonListener(button=mouse_button)  # type: ignore[return-value]
    return KeyboardHotkeyListener(hotkey=hotkey)  # type: ignore[return-value]
