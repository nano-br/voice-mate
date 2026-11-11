"""Starts an external provider when it is not running (`TTSConfig.external_start_command`,
`ExternalSttConfig.start_command`).

The provider is a service of its own (docs/tts-provider-protocol.md, docs/stt-provider-protocol.md):
VoiceMate only knows its URL. When the person also gives the command that starts it, the engine runs that command itself
whenever nothing listens at that URL: at startup, and again if the provider goes away later. So
a provider set up this way starts with VoiceMate, and VoiceMate starts with Windows. A provider
that is only slow to answer (a timeout) may well be running, so it is not started again.

The command runs on the engine's side (inside WSL when the engine runs there), through the
shell, without the engine's virtual environment and GPU tuning, detached from the engine: it is never stopped
by VoiceMate, so the provider keeps its models loaded across engine restarts. It is run at most
once every `RELAUNCH_AFTER_SECONDS`, and not while the process it started last is still running
(a provider that takes a while to open its port is not started twice, as long as the command
stays in the foreground). The command itself is never logged, only how it failed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Final, Literal

from app.i18n import _
from app.protocol.provider_http import ProviderError

# The shortest time between two runs of the start command (a provider opens its port in seconds).
RELAUNCH_AFTER_SECONDS: Final = 60.0

# What the engine's environment has for the engine alone: `poetry run` and `make` add the first
# ones, the engine adds the rest for its own GPU work (app/core/rocm_env.py, app/cli/wiring.py,
# app/main.py). Measured: a provider on the same GPU with this tuning takes about 20 s on each
# sentence length it has not seen yet, against 1 to 2 s without it.
_ENGINE_ONLY_ENV: Final = (
    "POETRY_ACTIVE",
    "MAKEFLAGS",
    "MFLAGS",
    "MAKELEVEL",
    "MAKEOVERRIDES",
    "ARGS",
    "PULSE_LATENCY_MSEC",
    "CT2_CUDA_ALLOCATOR",
)
_ENGINE_ONLY_ENV_PREFIXES: Final = ("MIOPEN_", "PYTORCH_TUNABLEOP_")

Spawn = Callable[[str], "subprocess.Popen[bytes]"]
# Which provider the launcher starts: it only changes what the log says.
ProviderService = Literal["tts", "stt"]


def provider_env() -> dict[str, str]:
    """The engine's environment without what is its own: a provider has its own Python and tuning."""
    env = {name: value for name, value in os.environ.items() if not name.startswith(_ENGINE_ONLY_ENV_PREFIXES)}
    for name in _ENGINE_ONLY_ENV:
        env.pop(name, None)
    venv = env.pop("VIRTUAL_ENV", None)
    if venv:
        scripts = os.path.join(venv, "Scripts" if sys.platform == "win32" else "bin")
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p != scripts)
    return env


def spawn_detached(command: str) -> subprocess.Popen[bytes]:
    """`command` through the shell, with no console, no inherited input or output, and out of
    the engine's process group (closing the engine does not stop it)."""
    if sys.platform == "win32":
        # Not DETACHED_PROCESS: with it Windows ignores CREATE_NO_WINDOW and a console program opens a window.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(  # noqa: S602 - the person's own command, run as the person typed it
            command,
            shell=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            cwd=os.path.expanduser("~"),
            env=provider_env(),
        )
    return subprocess.Popen(  # noqa: S603 - the person's own command, run as the person typed it
        ["/bin/sh", "-c", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        cwd=os.path.expanduser("~"),
        env=provider_env(),
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
        service: ProviderService = "tts",
    ) -> None:
        self._command = command.strip()
        self._service: ProviderService = service
        self._health = health
        self._spawn = spawn
        self._clock = clock
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._last_run: float | None = None
        self._failed = False

    @property
    def enabled(self) -> bool:
        return bool(self._command)

    @property
    def failed(self) -> bool:
        """The last run of the command failed and the next one is not due: no provider is on its way.
        A run that failed long ago says nothing: asking now runs the command again."""
        last = self._last_run
        return self._failed and last is not None and self._clock() - last < RELAUNCH_AFTER_SECONDS

    def _reap(self, process: subprocess.Popen[bytes]) -> None:
        self._failed = bool(process.wait())

    def start_soon(self) -> None:
        """`start_if_down` on its own thread: the caller (a request, the engine's startup) never waits."""
        if self.enabled:
            threading.Thread(target=self.start_if_down, name=f"{self._service}-provider-start", daemon=True).start()

    def start_if_down(self) -> bool:
        """Run the start command if the provider does not answer. True when it was run now."""
        if not self.enabled:
            return False
        with self._lock:
            if self._process is not None:
                code = self._process.poll()
                if code is None:
                    return False  # what was started last is still running (still opening its port)
                self._process = None
                if code != 0:
                    print(self._exited_message(code), file=sys.stderr)
            if self._last_run is not None and self._clock() - self._last_run < RELAUNCH_AFTER_SECONDS:
                return False
            try:
                self._health()
                return False  # it answers (ready or loading): nothing to start
            except ProviderError as exc:
                if not isinstance(exc.__cause__, ConnectionRefusedError):
                    return False  # it may be there, only unhappy or busy: starting another would not help
            self._failed = False
            self._last_run = self._clock()
            print(self._starting_message())
            try:
                self._process = self._spawn(self._command)
            except OSError as exc:
                self._failed = True
                print(self._failed_message(exc.strerror or type(exc).__name__), file=sys.stderr)
                return False
            threading.Thread(
                target=self._reap, args=(self._process,), name=f"{self._service}-provider-reap", daemon=True
            ).start()
            return True

    # --- what the log says (one literal per message, for the translation catalogs) -----------------------

    def _starting_message(self) -> str:
        if self._service == "stt":
            return _("[ExternalTranscriber] The STT provider is not running: starting it with the configured command.")
        return _("[ExternalSpeaker] The TTS provider is not running: starting it with the configured command.")

    def _exited_message(self, code: int) -> str:
        if self._service == "stt":
            return _("[ExternalTranscriber] ⚠ The STT provider start command exited with code {code}.").format(
                code=code
            )
        return _("[ExternalSpeaker] ⚠ The TTS provider start command exited with code {code}.").format(code=code)

    def _failed_message(self, detail: str) -> str:
        if self._service == "stt":
            return _("[ExternalTranscriber] ⚠ The STT provider start command failed: {detail}").format(detail=detail)
        return _("[ExternalSpeaker] ⚠ The TTS provider start command failed: {detail}").format(detail=detail)
