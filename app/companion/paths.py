"""Where the companion keeps its files (docs/companion-app.md, "Settings" and "Supervisor").

Windows: settings in %APPDATA%\\VoiceMate, logs and rendered cues in %LOCALAPPDATA%\\VoiceMate.
Linux: settings in ~/.config/voicemate, logs in ${XDG_STATE_HOME:-~/.local/state}/voicemate/logs,
cues in ~/.cache/voicemate/cues (XDG variables honored).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SETTINGS_FILE_NAME = "companion.toml"
ENGINE_LOG_NAME = "engine.log"
COMPANION_LOG_NAME = "companion.log"


def _env_dir(name: str, fallback: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else fallback


def config_dir() -> Path:
    if sys.platform == "win32":
        return _env_dir("APPDATA", Path.home() / "AppData" / "Roaming") / "VoiceMate"
    return _env_dir("XDG_CONFIG_HOME", Path.home() / ".config") / "voicemate"


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE_NAME


def _local_dir() -> Path:
    return _env_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local") / "VoiceMate"


def logs_dir() -> Path:
    if sys.platform == "win32":
        return _local_dir() / "logs"
    return _env_dir("XDG_STATE_HOME", Path.home() / ".local" / "state") / "voicemate" / "logs"


def cues_dir() -> Path:
    if sys.platform == "win32":
        return _local_dir() / "cues"
    return _env_dir("XDG_CACHE_HOME", Path.home() / ".cache") / "voicemate" / "cues"


def engine_log_path() -> Path:
    return logs_dir() / ENGINE_LOG_NAME


def companion_log_path() -> Path:
    return logs_dir() / COMPANION_LOG_NAME
