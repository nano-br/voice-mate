"""Camada de plataforma: detecção de ambiente + implementações por SO.

O core permanece agnóstico de SO; tudo que depende de Windows / Linux X11 /
Linux Wayland / WSL2 (hotkeys, clipboard, daemon HTTP) vive aqui, atrás dos
Protocols já existentes (`InputListener`, `ClipboardWriter`).
"""

from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind, TriggerKind

__all__ = ["PlatformKind", "TriggerKind", "default_trigger", "detect_platform"]
