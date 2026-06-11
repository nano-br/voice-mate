"""Hotkeys globais via evdev (/dev/input) — funciona em X11 E Wayland.

Lê os eventos de teclado direto do kernel, antes do display server, então o
compositor Wayland não consegue escondê-los. Requer que o usuário esteja no
grupo `input` (sem sudo):

    sudo usermod -aG input $USER && newgrp input

Não funciona no WSL2 (não há /dev/input mapeado para o teclado do Windows) —
lá o gatilho é o SocketTriggerListener + script do lado Windows.
"""

from __future__ import annotations

import selectors
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_GROUP_HINT = "sudo usermod -aG input $USER  # e re-logue (ou rode `newgrp input`)"

# Modificador lógico → keycodes evdev que o satisfazem.
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
    modifiers: frozenset[str]  # nomes lógicos: "ctrl", "alt", ...
    key_code: str  # ex.: "KEY_V"
    callback: Callable[[], None]


def parse_hotkey(hotkey: str, callback: Callable[[], None]) -> _ParsedHotkey:
    parts = [part.strip().lower() for part in hotkey.split("+") if part.strip()]
    if not parts:
        raise ValueError(f"Hotkey vazio: {hotkey!r}")
    modifiers = frozenset(part for part in parts[:-1] if part in _MODIFIER_CODES)
    if len(modifiers) != len(parts) - 1:
        unknown = [p for p in parts[:-1] if p not in _MODIFIER_CODES]
        raise ValueError(f"Modificador desconhecido em {hotkey!r}: {unknown}")
    return _ParsedHotkey(modifiers=modifiers, key_code=f"KEY_{parts[-1].upper()}", callback=callback)


class EvdevHotkeyListener:
    """Escuta chords de teclado em todos os teclados de /dev/input."""

    def __init__(self, bindings: dict[str, Callable[[], None]]) -> None:
        if not bindings:
            raise ValueError("EvdevHotkeyListener exige ao menos um binding")
        self._hotkeys = [parse_hotkey(hk, cb) for hk, cb in bindings.items()]
        self._stop_event = threading.Event()

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        try:
            import evdev
        except ImportError as exc:
            raise RuntimeError(
                "Pacote 'evdev' não instalado (necessário p/ hotkeys em Wayland). "
                "Instale com: poetry install --extras linux"
            ) from exc

        keyboards = self._open_keyboards(evdev)
        if not keyboards:
            raise RuntimeError(
                "Nenhum teclado legível em /dev/input. Adicione seu usuário ao grupo input:\n  " + _GROUP_HINT
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
        """No-op: hooks removidos sob carga são um problema exclusivo do Windows."""
        return None

    def stop(self) -> None:
        self._stop_event.set()

    # ─── internos ────────────────────────────────────────────────────────────

    @staticmethod
    def _open_keyboards(evdev: Any) -> list[Any]:  # noqa: ANN401 — módulo importado em runtime
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
            # Heurística de "é teclado": tem letras (KEY_A) e modificadores.
            if evdev.ecodes.KEY_A in key_codes and evdev.ecodes.KEY_LEFTCTRL in key_codes:
                keyboards.append(device)
            else:
                device.close()
        if not keyboards and permission_errors:
            raise RuntimeError(
                f"Sem permissão de leitura em {permission_errors} device(s) de /dev/input. "
                "Adicione seu usuário ao grupo input:\n  " + _GROUP_HINT
            )
        return keyboards

    def _handle_key_event(self, evdev: Any, event: Any, pressed: set[str]) -> None:  # noqa: ANN401
        key_event = evdev.categorize(event)
        code = key_event.keycode if isinstance(key_event.keycode, str) else key_event.keycode[0]
        if key_event.keystate == key_event.key_up:
            pressed.discard(code)
            return
        if key_event.keystate != key_event.key_down:
            return  # ignora key_hold (auto-repeat)
        pressed.add(code)
        for hotkey in self._hotkeys:
            if code != hotkey.key_code:
                continue
            if all(any(mod_code in pressed for mod_code in _MODIFIER_CODES[mod]) for mod in hotkey.modifiers):
                hotkey.callback()
