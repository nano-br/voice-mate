"""Start at login on Linux: an XDG autostart `.desktop` file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

DESKTOP_FILE_NAME: Final = "voicemate-companion.desktop"
_RESERVED: Final = set(" \t\n\"'\\><~|&;$*?#()`")


def desktop_file_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".config") / "autostart" / DESKTOP_FILE_NAME


def quote_exec_arg(arg: str) -> str:
    """Desktop Entry `Exec` quoting: double quotes; escape ", `, $ and \\ inside; % as %%."""
    arg = arg.replace("%", "%%")
    if arg and not any(char in _RESERVED for char in arg):
        return arg
    escaped = "".join("\\" + char if char in '"`$\\' else char for char in arg)
    return f'"{escaped}"'


def desktop_entry(command: list[str]) -> str:
    exec_line = " ".join(quote_exec_arg(arg) for arg in command)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=VoiceMate\n"
        "Comment=VoiceMate companion\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def autostart_enabled(*, path: Path | None = None) -> bool:
    return (path or desktop_file_path()).is_file()


def set_autostart(enabled: bool, command: list[str], *, path: Path | None = None) -> None:
    target = path or desktop_file_path()
    if not enabled:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(desktop_entry(command), encoding="utf-8")
