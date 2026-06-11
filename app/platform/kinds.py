"""Tipos enumerados da camada de plataforma (sem dependências — importável por todos)."""

from __future__ import annotations

from typing import Literal

# Ambiente de execução resolvido em runtime (detectado, persistido ou via --platform).
#   - windows: Windows nativo (cenário main: NVIDIA + libs keyboard/mouse).
#   - linux-x11: Linux nativo com sessão X11.
#   - linux-wayland: Linux nativo com sessão Wayland.
#   - wsl2: Linux dentro do WSL2 (WSLg) — áudio via PulseAudio RDP, hotkey via daemon.
PlatformKind = Literal["windows", "linux-x11", "linux-wayland", "wsl2"]

# Mecanismo de gatilho da gravação:
#   - keyboard-hooks: libs `keyboard`/`mouse` (hooks Win32 — só Windows).
#   - pynput: pynput.GlobalHotKeys (X11; não funciona em Wayland puro).
#   - evdev: /dev/input via evdev (qualquer display server; requer grupo `input`).
#   - socket: daemon HTTP local — o gatilho vem de fora (script Windows no WSL2,
#     ou qualquer automação que faça POST /trigger).
TriggerKind = Literal["keyboard-hooks", "pynput", "evdev", "socket"]
