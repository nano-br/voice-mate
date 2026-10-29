"""Streamed PCM playback on Linux: raw PCM into `pw-cat` (PipeWire) or `pacat` (PulseAudio).

The same players the cues use (app/companion/linux/sound.py) read raw PCM from stdin. The
process is told the format on its command line, is fed as the audio arrives and is killed to
silence it at once. When the queued audio has played is estimated from the bytes written (the
players do not report it), plus their buffer.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Final

LATENCY_S: Final = 0.15


def player_command(sample_rate: int, channels: int) -> list[str] | None:
    """The raw-PCM player found on this system, or None."""
    pw_cat = shutil.which("pw-cat")
    if pw_cat:
        return [pw_cat, "--playback", "--format", "s16", "--rate", str(sample_rate), "--channels", str(channels), "-"]
    pacat = shutil.which("pacat")  # paplay has no --playback: it would exit on the option
    if pacat:
        return [
            pacat,
            "--playback",
            "--raw",
            "--format=s16le",
            f"--rate={sample_rate}",
            f"--channels={channels}",
            f"--latency-msec={int(LATENCY_S * 1000)}",
        ]
    return None


class PipeOutput:
    """A `PcmOutput` (app/companion/voice.py) through a player process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._bytes_per_s = 0
        self._ends_at = 0.0  # monotonic time the written audio should be done playing

    def open(self, sample_rate: int, channels: int) -> None:
        command = player_command(sample_rate, channels)
        if command is None:
            raise OSError("no pw-cat or pacat found")
        with self._lock:
            self._process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._bytes_per_s = sample_rate * channels * 2
            self._ends_at = time.monotonic()

    def write(self, data: bytes) -> None:
        # The player's pipe blocks when it is full (it consumes in real time): write outside the
        # lock so `flush()` can kill the process, which unblocks this write with a broken pipe.
        with self._lock:
            process = self._process
        if process is None or process.stdin is None:
            return
        try:
            process.stdin.write(data)
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            with self._lock:
                if self._process is not process:  # flush() or close() ended it on purpose
                    return
            raise OSError(f"the audio player stopped: {exc}") from exc
        with self._lock:
            self._ends_at = max(self._ends_at, time.monotonic()) + len(data) / self._bytes_per_s

    def commit(self) -> None:
        return None

    def pending(self) -> bool:
        with self._lock:
            return self._process is not None and time.monotonic() < self._ends_at + LATENCY_S

    def flush(self) -> None:
        self._kill()

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()

    def _kill(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is not None:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
