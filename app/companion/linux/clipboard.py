"""Clipboard on Linux (manual copies from Recent/Pending; the engine owns delivery there).

`wl-copy`/`wl-paste` on Wayland, `xclip` on X11. Both copy tools fork a background process
that serves the selection, so their output goes to /dev/null (a pipe would never close).
Writes are verified by reading back, like on Windows.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Final

log = logging.getLogger(__name__)

ATTEMPTS: Final = 5
BACKOFF_S: Final = 0.06
TOOL_TIMEOUT_S: Final = 3.0


def clipboard_commands() -> tuple[list[str], list[str]] | None:
    """(copy command, paste command), or None when no clipboard tool is installed."""
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy") and shutil.which("wl-paste"):
        return ["wl-copy"], ["wl-paste", "--no-newline"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard"], ["xclip", "-selection", "clipboard", "-o"]
    return None


def _matches(current: str, expected: str) -> bool:
    return current == expected or current.rstrip("\n") == expected.rstrip("\n")


def set_text_verified(text: str, commands: tuple[list[str], list[str]] | None = None) -> bool:
    tools = commands or clipboard_commands()
    if tools is None:
        log.warning("no clipboard tool found (install wl-clipboard or xclip)")
        return False
    copy, paste = tools
    for attempt in range(1, ATTEMPTS + 1):
        try:
            subprocess.run(
                copy,
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=TOOL_TIMEOUT_S,
                check=False,
            )
            time.sleep(BACKOFF_S * attempt)
            current = subprocess.run(paste, capture_output=True, timeout=TOOL_TIMEOUT_S, check=False).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("clipboard tool failed: %s", exc)
            continue
        if _matches(current.decode("utf-8", errors="replace"), text):
            return True
    return False


class LinuxClipboard:
    def deliver(self, text: str, done: Callable[[bool], None]) -> None:
        def work() -> None:
            ok = set_text_verified(text)
            try:
                done(ok)
            except Exception:  # noqa: BLE001 - the callback must not kill the worker silently
                log.exception("clipboard delivery callback failed")

        threading.Thread(target=work, name="companion-clipboard", daemon=True).start()
