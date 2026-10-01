"""Start at login on Windows: the `VoiceMate` value under HKCU\\...\\CurrentVersion\\Run.

The registry is the source of truth for "start at login" (the installer may set it on its
own): the controller reads it on load and writes it on apply. The data is
`"<exe>" --autostart` (the executable always quoted).
"""

from __future__ import annotations

import subprocess
import sys
import winreg
from typing import Final

from app.companion.contract import AUTOSTART_RUN_VALUE

assert sys.platform == "win32"  # also tells type checkers this module is Windows-only

RUN_KEY: Final = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME: Final = AUTOSTART_RUN_VALUE  # shared with the installer


def command_line(command: list[str]) -> str:
    """`"<exe>" arg ...`: the executable always in quotes, the arguments as Windows expects."""
    executable, *arguments = command
    line = f'"{executable}"'
    return f"{line} {subprocess.list2cmdline(arguments)}" if arguments else line


def set_autostart(enabled: bool, command: list[str], *, key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> None:
    """Write (or delete) the Run value. Raises OSError when the registry refuses."""
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, access) as key:
        if enabled:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command_line(command))
            return
        try:
            winreg.DeleteValue(key, value_name)
        except FileNotFoundError:
            pass


def autostart_command_line(*, key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            value, kind = winreg.QueryValueEx(key, value_name)
    except FileNotFoundError:
        return None
    return value if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str) else None


def autostart_enabled(*, key_path: str = RUN_KEY, value_name: str = VALUE_NAME) -> bool:
    return bool(autostart_command_line(key_path=key_path, value_name=value_name))
