"""Restart the companion: start a detached copy that takes over once this one has quit.

Used when a setting only applies at startup (the UI language). The order is: spawn the
new process first (a failure leaves this one running untouched), then quit like "Quit
VoiceMate" does (the controller stops the engine it started). The new process gets
`--after-restart`: instead of forwarding "show" to the instance that is quitting, it
waits for the single-instance lock (`app/companion/main.py`, `RESTART_WAIT_S`), which
this process releases when it exits. The engine restarts along with the companion.

The child must outlive its parent: on Windows it is created detached, in its own
process group and, when allowed, outside any job object the parent belongs to (a job
with kill-on-close would take it down with us); on Linux it gets its own session.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

log = logging.getLogger(__name__)

AFTER_RESTART_FLAG: Final = "--after-restart"
# Not repeated in the new process: a forwarded command, and the sign-in start (the restart
# was asked from the open UI, so the new instance shows its window).
_DROPPED_FLAGS: Final = frozenset({"--autostart", AFTER_RESTART_FLAG})
_DROPPED_WITH_VALUE: Final = "--command"
_MODULE: Final = "app.companion.main"
# Source runs start `python -m app.companion.main` from the repository root.
_SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]

# Win32 process creation flags (subprocess only names some of them on Windows).
DETACHED_PROCESS: Final = 0x00000008
CREATE_NEW_PROCESS_GROUP: Final = 0x00000200
CREATE_BREAKAWAY_FROM_JOB: Final = 0x01000000

# `subprocess.Popen` or a test double: called with the argv and keyword arguments.
Popen = Callable[..., object]


def carried_args(args: Sequence[str]) -> list[str]:
    """The original command line arguments (without the program) worth repeating."""
    kept: list[str] = []
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            continue
        if arg == _DROPPED_WITH_VALUE:
            skip_value = True
            continue
        if arg.startswith(_DROPPED_WITH_VALUE + "=") or arg in _DROPPED_FLAGS:
            continue
        kept.append(arg)
    return kept


def relaunch_argv(executable: str, args: Sequence[str], frozen: bool) -> list[str]:
    """The new process's command line: the frozen executable itself, or the module."""
    program = [executable] if frozen else [executable, "-m", _MODULE]
    return [*program, *carried_args(args), AFTER_RESTART_FLAG]


def spawn_detached(
    argv: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    platform: str = sys.platform,
    popen: Popen = subprocess.Popen,
) -> None:
    """Start `argv` so it survives this process. Raises OSError when it cannot start."""
    common: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "cwd": cwd,
        "env": None if env is None else dict(env),
    }
    if platform == "win32":
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            popen(list(argv), creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **common)
        except OSError:
            # Inside a job that does not allow breakaway (access denied): a plain detached
            # process is the best we can do there.
            log.info("could not break away from the job; starting the new instance without it")
            popen(list(argv), creationflags=flags, **common)
        return
    popen(list(argv), start_new_session=True, **common)


def relaunch_companion(args: Sequence[str], popen: Popen = subprocess.Popen) -> None:
    """Start a new companion with `args` (this process's arguments, without the program)
    plus `--after-restart`. Raises OSError when it cannot start."""
    frozen = bool(getattr(sys, "frozen", False))
    argv = relaunch_argv(sys.executable, args, frozen)
    env: dict[str, str] | None = None
    if frozen:
        # PyInstaller: a child of a frozen app that runs the same executable must set
        # itself up from scratch, as a new top-level instance.
        env = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    cwd = None if frozen else str(_SOURCE_ROOT)
    log.info("starting a new instance: %s", argv)
    spawn_detached(argv, cwd=cwd, env=env, popen=popen)
