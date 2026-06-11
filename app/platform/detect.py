"""Detecção do ambiente de execução. Só stdlib — usado também pelo setup."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.platform.kinds import PlatformKind, TriggerKind

_PROC_VERSION = Path("/proc/version")


def detect_platform() -> PlatformKind:
    """Resolve o ambiente atual.

    A checagem de WSL vem antes da de Wayland de propósito: o WSLg exporta
    WAYLAND_DISPLAY dentro do WSL2, mas o tratamento correto lá é o de WSL
    (mic via PulseAudio RDP, gatilho via daemon), não o de Wayland nativo.
    """
    if sys.platform == "win32":
        return "windows"
    if "microsoft" in _read_proc_version().lower():
        return "wsl2"
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if os.environ.get("WAYLAND_DISPLAY") or session_type == "wayland":
        return "linux-wayland"
    return "linux-x11"


def default_trigger(platform: PlatformKind) -> TriggerKind:
    """Gatilho default por ambiente (sobrescritível via --trigger/persisted)."""
    if platform == "windows":
        return "keyboard-hooks"
    if platform == "linux-x11":
        return "pynput"
    if platform == "linux-wayland":
        return "evdev"
    return "socket"


def _read_proc_version() -> str:
    try:
        return _PROC_VERSION.read_text(encoding="utf-8")
    except OSError:
        return ""
