"""Where the companion keeps its files (docs/companion-app.md, "Settings" and "Supervisor").

Windows: settings in %APPDATA%\\VoiceMate; logs, rendered cues and the Not copied list
(pending.json) in %LOCALAPPDATA%\\VoiceMate.
Linux: settings in ~/.config/voicemate; logs and pending.json in
${XDG_STATE_HOME:-~/.local/state}/voicemate, cues in ~/.cache/voicemate/cues (XDG
variables honored).

The environment variable always wins, and the home directory is only looked up when it
is missing: a process started without USERPROFILE/HOME (PowerShell
`Start-Process -UseNewEnvironment`) still finds %APPDATA% and %LOCALAPPDATA%. With
neither available, `DataDirError` names what to set instead of a bare
"Could not determine home directory".
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

SETTINGS_FILE_NAME = "companion.toml"
ENGINE_LOG_NAME = "engine.log"
COMPANION_LOG_NAME = "companion.log"
PENDING_FILE_NAME = "pending.json"


class DataDirError(RuntimeError):
    """Neither the folder's environment variable nor a home directory is available."""


def home_dir() -> Path | None:
    """The user's home directory, or None when the OS cannot tell (no USERPROFILE/HOME)."""
    try:
        return Path.home()
    except RuntimeError:
        return None


def _env_dir(name: str, fallback: Callable[[Path], Path]) -> Path:
    """`$name` when set, else `fallback(home)`. The home directory is only looked up
    when the variable is missing, so it never breaks a path the variable already gives."""
    value = os.environ.get(name, "").strip()
    if value:
        return Path(value)
    home = home_dir()
    if home is None:
        home_variable = "USERPROFILE" if sys.platform == "win32" else "HOME"
        raise DataDirError(
            f"Cannot locate the VoiceMate data folder: {name} is not set and the home directory "
            f"is unknown. Set {name} or {home_variable} in the environment that starts VoiceMate."
        )
    return fallback(home)


def config_dir() -> Path:
    if sys.platform == "win32":
        return _env_dir("APPDATA", lambda home: home / "AppData" / "Roaming") / "VoiceMate"
    return _env_dir("XDG_CONFIG_HOME", lambda home: home / ".config") / "voicemate"


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE_NAME


def state_dir() -> Path:
    """Per-user local data that is not settings: logs and the Not copied list."""
    if sys.platform == "win32":
        return _env_dir("LOCALAPPDATA", lambda home: home / "AppData" / "Local") / "VoiceMate"
    return _env_dir("XDG_STATE_HOME", lambda home: home / ".local" / "state") / "voicemate"


def logs_dir() -> Path:
    return state_dir() / "logs"


def cues_dir() -> Path:
    if sys.platform == "win32":
        return state_dir() / "cues"
    return _env_dir("XDG_CACHE_HOME", lambda home: home / ".cache") / "voicemate" / "cues"


def engine_log_path() -> Path:
    return logs_dir() / ENGINE_LOG_NAME


def companion_log_path() -> Path:
    return logs_dir() / COMPANION_LOG_NAME


def pending_path() -> Path:
    """The Not copied list (transcription text): see app/companion/pending_store.py."""
    return state_dir() / PENDING_FILE_NAME
