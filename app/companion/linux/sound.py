"""Cue playback on Linux: `pw-play` (PipeWire) or `paplay` (PulseAudio). One sound at a time."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class LinuxSound:
    def __init__(self) -> None:
        self._player = shutil.which("pw-play") or shutil.which("paplay")
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def available(self) -> bool:
        return self._player is not None

    def play(self, path: Path) -> None:
        if self._player is None:
            log.debug("no audio player (pw-play/paplay) found")
            return
        with self._lock:
            self._stop_locked()
            self._process = subprocess.Popen(
                [self._player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
