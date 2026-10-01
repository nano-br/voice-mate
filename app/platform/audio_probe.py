"""Background health probe of the audio server, reported in the daemon's `/health`.

On WSL2 all audio (mic and speakers) goes through WSLg's PulseAudio-over-RDP. When
it dies (seen after the Windows microphone disappeared, and after sleep/resume),
every open hangs or times out and nothing inside the distro brings it back: only
`wsl --shutdown` does. The daemon can't restart its own VM, so it just REPORTS the
state; the Windows side (the hotkeys script today, a supervising launcher later)
decides whether a WSL restart is worth it (e.g. only if Windows actually has a
microphone).

The probe is `pactl info` with a timeout: it talks to the server without opening
the microphone (no mic-in-use indicator on Windows) and hangs exactly when the
server is stuck. No `pactl` installed → "unknown" (the launcher ignores it).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Literal

AudioHealth = Literal["ok", "down", "unknown"]

# Runs a command with a timeout; raises subprocess.TimeoutExpired / OSError like subprocess.run.
Runner = Callable[[list[str], float], int]


def _run(cmd: list[str], timeout: float) -> int:
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False).returncode


class AudioServerProbe:
    """Periodically checks whether the PulseAudio server answers; thread-safe `state`."""

    def __init__(self, interval: float = 20.0, timeout: float = 5.0, runner: Runner = _run) -> None:
        self._interval = interval
        self._timeout = timeout
        self._runner = runner
        self._state: AudioHealth = "unknown"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def state(self) -> AudioHealth:
        with self._lock:
            return self._state

    def probe_once(self) -> AudioHealth:
        pactl = shutil.which("pactl")
        if pactl is None:
            result: AudioHealth = "unknown"
        else:
            try:
                result = "ok" if self._runner([pactl, "info"], self._timeout) == 0 else "down"
            except subprocess.TimeoutExpired:
                result = "down"
            except OSError:
                result = "unknown"
        with self._lock:
            self._state = result
        return result

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="AudioProbe")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.probe_once()
            self._stop.wait(self._interval)
