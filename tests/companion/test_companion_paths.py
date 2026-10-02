"""Where the companion keeps its files, and the lazy home directory fallback."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.companion import paths
from app.companion.supervisor.backend import read_local_token
from app.companion.supervisor.local import LocalBackend

WINDOWS = sys.platform == "win32"
CONFIG_VAR = "APPDATA" if WINDOWS else "XDG_CONFIG_HOME"
STATE_VAR = "LOCALAPPDATA" if WINDOWS else "XDG_STATE_HOME"
CACHE_VAR = "LOCALAPPDATA" if WINDOWS else "XDG_CACHE_HOME"
HOME_VARS = ("USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH")


def _no_home(cls: type[Path]) -> Path:
    raise RuntimeError("Could not determine home directory.")


@pytest.fixture
def no_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process started without USERPROFILE/HOME (PowerShell `Start-Process -UseNewEnvironment`)."""
    for variable in HOME_VARS:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(_no_home))


def _set_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(CONFIG_VAR, str(tmp_path / "config"))
    monkeypatch.setenv(STATE_VAR, str(tmp_path / "state"))
    monkeypatch.setenv(CACHE_VAR, str(tmp_path / ("state" if WINDOWS else "cache")))


def test_files_live_under_the_platform_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_dirs(monkeypatch, tmp_path)
    app_dir = "VoiceMate" if WINDOWS else "voicemate"
    assert paths.settings_path() == tmp_path / "config" / app_dir / "companion.toml"
    assert paths.state_dir() == tmp_path / "state" / app_dir
    assert paths.logs_dir() == tmp_path / "state" / app_dir / "logs"
    assert paths.companion_log_path() == paths.logs_dir() / "companion.log"
    assert paths.engine_log_path() == paths.logs_dir() / "engine.log"
    assert paths.cues_dir() == tmp_path / ("state" if WINDOWS else "cache") / app_dir / "cues"


def test_the_environment_wins_without_a_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_home: None
) -> None:
    _set_dirs(monkeypatch, tmp_path)
    for path in (paths.settings_path(), paths.logs_dir(), paths.cues_dir()):
        assert str(path).startswith(str(tmp_path))
    assert paths.home_dir() is None


def test_without_the_variable_the_home_directory_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (CONFIG_VAR, STATE_VAR, CACHE_VAR):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if WINDOWS:
        assert paths.config_dir() == tmp_path / "AppData" / "Roaming" / "VoiceMate"
        assert paths.logs_dir() == tmp_path / "AppData" / "Local" / "VoiceMate" / "logs"
    else:
        assert paths.config_dir() == tmp_path / ".config" / "voicemate"
        assert paths.logs_dir() == tmp_path / ".local" / "state" / "voicemate" / "logs"
        assert paths.cues_dir() == tmp_path / ".cache" / "voicemate" / "cues"


def test_with_neither_the_error_says_what_to_set(monkeypatch: pytest.MonkeyPatch, no_home: None) -> None:
    for variable in (CONFIG_VAR, STATE_VAR, CACHE_VAR):
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(paths.DataDirError, match=CONFIG_VAR) as raised:
        paths.settings_path()
    assert isinstance(raised.value, RuntimeError)
    assert ("USERPROFILE" if WINDOWS else "HOME") in str(raised.value)
    with pytest.raises(paths.DataDirError, match=STATE_VAR):
        paths.companion_log_path()
    # A blank variable counts as missing.
    monkeypatch.setenv(STATE_VAR, "   ")
    with pytest.raises(paths.DataDirError, match=STATE_VAR):
        paths.logs_dir()


def test_engine_helpers_tolerate_a_missing_home(tmp_path: Path, no_home: None) -> None:
    assert read_local_token() is None
    assert LocalBackend(47821, tmp_path / "engine.log").detect_engine_dir() is None
