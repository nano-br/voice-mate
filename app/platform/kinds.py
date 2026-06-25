"""Enumerated types for the platform layer (no dependencies — importable everywhere)."""

from __future__ import annotations

from typing import Literal

# Runtime-resolved execution environment (detected, persisted, or via --platform).
#   - windows: native Windows (main scenario: NVIDIA + keyboard/mouse libs).
#   - linux-x11: native Linux with an X11 session.
#   - linux-wayland: native Linux with a Wayland session.
#   - wsl2: Linux inside WSL2 (WSLg) — audio via PulseAudio RDP, hotkey via daemon.
PlatformKind = Literal["windows", "linux-x11", "linux-wayland", "wsl2"]

# Recording trigger mechanism:
#   - keyboard-hooks: `keyboard`/`mouse` libs (Win32 hooks — Windows only).
#   - pynput: pynput.GlobalHotKeys (X11; does not work on pure Wayland).
#   - evdev: /dev/input via evdev (any display server; requires the `input` group).
#   - socket: local HTTP daemon — the trigger comes from outside (Windows-side
#     script under WSL2, or any automation that does POST /trigger).
TriggerKind = Literal["keyboard-hooks", "pynput", "evdev", "socket"]
