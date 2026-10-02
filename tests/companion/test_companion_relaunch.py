"""Restarting the companion: the new process's command line and how it is started.

Never starts a real process: `popen` is a recorder.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.companion import relaunch
from app.companion.relaunch import (
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    carried_args,
    relaunch_argv,
    spawn_detached,
)

_ROOT = Path(__file__).resolve().parents[2]
_DETACHED = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


class _Popen:
    def __init__(self, fail_breakaway: bool = False, fail_always: bool = False) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.fail_breakaway = fail_breakaway
        self.fail_always = fail_always

    def __call__(self, argv: list[str], **kwargs: object) -> object:
        self.calls.append((argv, kwargs))
        flags = kwargs.get("creationflags", 0)
        breakaway = isinstance(flags, int) and bool(flags & CREATE_BREAKAWAY_FROM_JOB)
        if self.fail_always or (self.fail_breakaway and breakaway):
            raise PermissionError(5, "Access is denied")
        return object()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], []),
        (["--autostart"], []),
        (["--command", "settings"], []),
        (["--command=quit", "--demo"], ["--demo"]),
        (["--demo", "--autostart", "--after-restart"], ["--demo"]),
    ],
)
def test_carried_args_drop_commands_and_the_sign_in_start(args: list[str], expected: list[str]) -> None:
    assert carried_args(args) == expected


def test_relaunch_argv_frozen_runs_the_executable_itself() -> None:
    argv = relaunch_argv(r"C:\Program Files\VoiceMate\VoiceMate.exe", ["--autostart", "--demo"], frozen=True)
    assert argv == [r"C:\Program Files\VoiceMate\VoiceMate.exe", "--demo", "--after-restart"]


def test_relaunch_argv_source_runs_the_module() -> None:
    argv = relaunch_argv("/venv/bin/python", ["--command", "show"], frozen=False)
    assert argv == ["/venv/bin/python", "-m", "app.companion.main", "--after-restart"]


def test_windows_spawn_is_detached_and_breaks_away_from_the_job() -> None:
    popen = _Popen()
    spawn_detached(["VoiceMate.exe", "--after-restart"], platform="win32", popen=popen)
    ((argv, kwargs),) = popen.calls
    assert argv == ["VoiceMate.exe", "--after-restart"]
    assert kwargs["creationflags"] == _DETACHED | CREATE_BREAKAWAY_FROM_JOB
    assert kwargs["close_fds"] is True
    assert "start_new_session" not in kwargs
    for stream in ("stdin", "stdout", "stderr"):
        assert kwargs[stream] == subprocess.DEVNULL


def test_windows_spawn_falls_back_without_breakaway() -> None:
    popen = _Popen(fail_breakaway=True)
    spawn_detached(["VoiceMate.exe"], platform="win32", popen=popen)
    flags = [kwargs["creationflags"] for _argv, kwargs in popen.calls]
    assert flags == [_DETACHED | CREATE_BREAKAWAY_FROM_JOB, _DETACHED]


def test_windows_spawn_failure_raises() -> None:
    with pytest.raises(OSError):
        spawn_detached(["VoiceMate.exe"], platform="win32", popen=_Popen(fail_always=True))


def test_linux_spawn_gets_its_own_session() -> None:
    popen = _Popen()
    spawn_detached(["python", "-m", "app.companion.main"], platform="linux", popen=popen)
    ((_argv, kwargs),) = popen.calls
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert "creationflags" not in kwargs


def test_relaunch_companion_from_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    popen = _Popen()
    relaunch.relaunch_companion(["--autostart", "--demo"], popen=popen)
    ((argv, kwargs),) = popen.calls
    assert argv == [sys.executable, "-m", "app.companion.main", "--demo", "--after-restart"]
    assert kwargs["cwd"] == str(_ROOT)
    assert kwargs["env"] is None


def test_relaunch_companion_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    popen = _Popen()
    relaunch.relaunch_companion(["--command", "settings"], popen=popen)
    ((argv, kwargs),) = popen.calls
    assert argv == [sys.executable, "--after-restart"]
    assert kwargs["cwd"] is None
    env = kwargs["env"]
    assert isinstance(env, dict) and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_relaunch_companion_reports_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    with pytest.raises(OSError):
        relaunch.relaunch_companion([], popen=_Popen(fail_always=True))
