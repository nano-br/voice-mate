"""Per-platform trigger listeners (all satisfy the InputListener Protocol)."""

from app.platform.listeners.evdev_listener import EvdevHotkeyListener
from app.platform.listeners.pynput_listener import PynputHotkeyListener
from app.platform.listeners.socket_trigger_listener import SocketTriggerListener

__all__ = ["EvdevHotkeyListener", "PynputHotkeyListener", "SocketTriggerListener"]
