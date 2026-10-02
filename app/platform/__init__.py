"""Platform layer: environment detection + per-OS implementations.

The core stays OS-agnostic; everything that depends on Windows / Linux X11 /
Linux Wayland / WSL2 (hotkeys, clipboard, HTTP daemon) lives here, behind the
existing Protocols (`InputListener`, `ClipboardWriter`).
"""

from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind, TriggerKind

__all__ = ["PlatformKind", "TriggerKind", "default_trigger", "detect_platform"]
