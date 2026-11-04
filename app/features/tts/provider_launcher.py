"""Starts the external TTS provider when it is not running (`TTSConfig.external_start_command`).

The provider is a service of its own (docs/tts-provider-protocol.md): VoiceMate only knows its
URL. When the person also gives the command that starts it, the engine runs that command itself
whenever the provider does not answer: at startup, and again when it stops answering later. So
a provider set up this way starts with VoiceMate, and VoiceMate starts with Windows.

The command runs in the engine's own environment (inside WSL when the engine runs there),
through the shell, detached from the engine: it is never stopped by VoiceMate, so the provider
keeps its models loaded across engine restarts. It is run at most once every
`RELAUNCH_AFTER_SECONDS`, and not while the process it started last is still running (a
provider that takes a while to open its port is not started twice). The command itself is never
logged: it may hold a password or a token.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Final

from app.i18n import _
from app.protocol.tts_provider import ProviderError

# The shortest time between two runs of the start command (a provider opens its port in seconds).
RELAUNCH_AFTER_SECONDS: Final = 60.0

Spawn = Callable[[str], "subprocess.Popen[bytes]"]


def spawn_detached(command: str) -> subprocess.Popen[bytes]:
    """`command` through the shell, with no console, no inherited input or output, and out of
    the engine's process group (closing the engine does not stop it)."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(  # noqa: S602 - the person's own command, run as the person typed it
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    return subprocess.Popen(  # noqa: S603 - the person's own command, run as the person typed it
        ["/bin/sh", "-c", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        cwd=os.path.expanduser("~"),
    )


class ProviderLauncher:
    """Runs the start command when the provider does not answer; see the module docstring."""

    def __init__(
        self,
        command: str,
        health: Callable[[], str],
        *,
        spawn: Spawn = spawn_detached,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._command = command.strip()
        self._health = health
        self._spawn = spawn
        self._clock = clock
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._last_run: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._command)

    def start_soon(self) -> None:
        """`start_if_down` on its own thread: the caller (a request, the engine's startup) never waits."""
        if self.enabled:
            threading.Thread(target=self.start_if_down, name="tts-provider-start", daemon=True).start()

    def start_if_down(self) -> bool:
        """Run the start command if the provider does not answer. True when it was run now."""
        if not self.enabled:
            return False
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False  # what was started last is still running (still opening its port)
            if self._last_run is not None and self._clock() - self._last_run < RELAUNCH_AFTER_SECONDS:
                return False
            try:
                self._health()
                return False  # it answers (ready or loading): nothing to start
            except ProviderError as exc:
                if exc.kind != "unreachable":
                    return False  # it is there, only unhappy: starting another would not help
            self._last_run = self._clock()
            print(_("[ExternalSpeaker] The TTS provider is not running: starting it with the configured command."))
            try:
                self._process = self._spawn(self._command)
            except OSError as exc:
                print(
                    _("[ExternalSpeaker] ⚠ The TTS provider start command failed: {detail}").format(
                        detail=exc.strerror or type(exc).__name__
                    ),
                    file=sys.stderr,
                )
                return False
            return True
