"""OS services the controller needs, bundled per platform.

The Windows and Linux implementations live under `win/` and `linux/` and are imported
lazily by `create_desktop`, so this module (and the controller) import on every OS.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.companion.contract import HotkeyCheck

log = logging.getLogger(__name__)


class HotkeyHost(Protocol):
    """Global hotkeys (Windows: the Win32 thread)."""

    def start(self, on_hotkey: Callable[[str], None]) -> None: ...

    def stop(self, timeout_s: float = 3.0) -> None: ...

    def register(self, bindings: Mapping[str, str], *, atomic: bool = False) -> dict[str, HotkeyCheck]: ...

    def check(self, chord: str) -> HotkeyCheck: ...

    def registered(self) -> dict[str, str]:
        """flow -> chord actually held right now (empty while suspended)."""

    def register_missing(self) -> dict[str, HotkeyCheck]:
        """Retry only the desired chords not held yet, never releasing held ones (probe-only
        while suspended). Verdicts for the missing flows; {} = everything is held."""

    def suspend(self, suspended: bool) -> None:
        """Non-blocking; later calls (register, check) see its effect."""


class Clipboard(Protocol):
    def deliver(self, text: str, done: Callable[[bool], None]) -> None:
        """Write `text`, verify it, then call `done(ok)` (from any thread)."""


class SoundOutput(Protocol):
    def play(self, path: Path) -> None: ...

    def stop(self) -> None: ...


def _no_autostart(enabled: bool, command: list[str]) -> None:
    del enabled, command


def _autostart_unknown() -> bool | None:
    return None


def _no_open(path: Path) -> None:
    log.info("open %s", path)


@dataclass
class Desktop:
    clipboard: Clipboard
    sound: SoundOutput
    mic_count: Callable[[], int] = field(default=lambda: -1)
    set_autostart: Callable[[bool, list[str]], None] = _no_autostart
    # The OS state of "start at login" (the source of truth); None = unknown.
    autostart_enabled: Callable[[], bool | None] = _autostart_unknown
    open_path: Callable[[Path], None] = _no_open
    hotkeys: HotkeyHost | None = None
    # Windows 11: keep (True) or stop keeping (False) the tray icon on the taskbar instead
    # of the overflow. Returns True once the OS has an entry for our icon (stop retrying).
    # None: the OS has no such setting (Linux; Windows 10 just never finds an entry).
    set_tray_icon_promoted: Callable[[bool], bool] | None = None
    # The OS lets the user pin the app from the Start menu (Windows): first-run tip.
    taskbar_pin_tip: bool = False

    def start(self, on_hotkey: Callable[[str], None]) -> None:
        if self.hotkeys is not None:
            self.hotkeys.start(on_hotkey)

    def stop(self) -> None:
        if self.hotkeys is not None:
            self.hotkeys.stop()
        try:
            self.sound.stop()
        except Exception:  # noqa: BLE001 - shutting down
            log.exception("stopping the sound failed")


def autostart_command() -> list[str]:
    """The command that starts the companion at login.

    Frozen: the executable itself. From source: the venv's windowless interpreter running
    the module with the checkout on `sys.path` (at login the working directory is not the
    repository, so a plain `-m app.companion.main` would fail with ModuleNotFoundError)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--autostart"]
    python = Path(sys.executable)
    if sys.platform == "win32":
        windowless = python.with_name("pythonw.exe")
        if windowless.is_file():
            python = windowless
    root = str(Path(__file__).resolve().parents[2])
    bootstrap = (
        f"import runpy, sys; sys.path.insert(0, {root!r}); "
        "runpy.run_module('app.companion.main', run_name='__main__', alter_sys=True)"
    )
    return [str(python), "-c", bootstrap, "--autostart"]


def _open_path(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - opens Explorer on our own folder
        return
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def create_desktop() -> Desktop:
    if sys.platform == "win32":
        from app.companion.win.audio_devices import active_capture_count as windows_mic_count
        from app.companion.win.autostart import autostart_enabled as windows_autostart_enabled
        from app.companion.win.autostart import set_autostart as windows_autostart
        from app.companion.win.hotkeys_clipboard import HotkeyClipboardThread
        from app.companion.win.sound import WinSound
        from app.companion.win.tray_visibility import set_tray_icon_promoted

        thread = HotkeyClipboardThread()
        return Desktop(
            clipboard=thread,
            sound=WinSound(),
            mic_count=windows_mic_count,
            set_autostart=lambda enabled, command: windows_autostart(enabled, command),
            autostart_enabled=lambda: windows_autostart_enabled(),
            open_path=_open_path,
            hotkeys=thread,
            set_tray_icon_promoted=lambda promoted: set_tray_icon_promoted(promoted),
            taskbar_pin_tip=True,
        )
    from app.companion.linux.audio_devices import active_capture_count as linux_mic_count
    from app.companion.linux.autostart import autostart_enabled as linux_autostart_enabled
    from app.companion.linux.autostart import set_autostart as linux_autostart
    from app.companion.linux.clipboard import LinuxClipboard
    from app.companion.linux.sound import LinuxSound

    return Desktop(
        clipboard=LinuxClipboard(),
        sound=LinuxSound(),
        mic_count=linux_mic_count,
        set_autostart=lambda enabled, command: linux_autostart(enabled, command),
        autostart_enabled=lambda: linux_autostart_enabled(),
        open_path=_open_path,
    )
