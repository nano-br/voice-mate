"""Audio player via `paplay` (PulseAudio) — robust on WSLg.

Why not sounddevice/PortAudio here: on WSL2 the audio output is PulseAudio over
RDP (WSLg), and PortAudio's callback stream is fragile on that transport —
underruns destabilize the stream and a stop/close operation can HANG
(`paTimedOut`), freezing the whole app (not even Ctrl+C kills it). `paplay` reads
raw PCM from stdin and lets PulseAudio itself handle the buffer (native, no
callback), so there's no deadlock and the process is always killable.

Same interface as `AudioPlayer` (ensure_started/feed/drain/abort/close). A writer
thread consumes the queue and writes to `paplay`'s stdin — so `feed()` doesn't
block (keeps the pipeline: synthesize the next sentence while the current one
plays). All waits have a timeout: the app never hangs on shutdown.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading

import numpy as np
from numpy.typing import NDArray

_SENTINEL = None


def paplay_available() -> bool:
    return shutil.which("paplay") is not None


class PaplayPlayer:
    """Play mono float32 chunks by sending raw PCM to `paplay`."""

    def __init__(self) -> None:
        self._queue: queue.Queue[NDArray[np.float32] | None] = queue.Queue()
        self._proc: subprocess.Popen[bytes] | None = None
        self._writer: threading.Thread | None = None
        self._sample_rate: int | None = None
        self._lock = threading.Lock()

    def ensure_started(self, sample_rate: int) -> None:
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None
            if alive and self._sample_rate == sample_rate:
                return
        self.start(sample_rate)

    def start(self, sample_rate: int) -> None:
        self._stop_process()
        proc = subprocess.Popen(  # noqa: S603
            ["paplay", "--raw", "--format=float32le", f"--rate={sample_rate}", "--channels=1"],  # noqa: S607
            stdin=subprocess.PIPE,
        )
        with self._lock:
            self._drain_queue()
            self._proc = proc
            self._sample_rate = sample_rate
            self._writer = threading.Thread(target=self._write_loop, args=(proc,), daemon=True, name="Paplay")
            self._writer.start()

    def feed(self, chunk: NDArray[np.float32]) -> None:
        self._queue.put(np.ascontiguousarray(chunk, dtype=np.float32).reshape(-1))

    def drain(self, timeout: float | None = 60.0) -> bool:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._sample_rate = None
        if proc is None:
            return True
        self._queue.put(_SENTINEL)  # writer closes stdin after draining the queue
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            self._kill(proc)
            return False

    def abort(self) -> None:
        self._stop_process()

    def close(self) -> None:
        self._stop_process()

    # ── internals ─────────────────────────────────────────────────────────────

    def _write_loop(self, proc: subprocess.Popen[bytes]) -> None:
        stdin = proc.stdin
        if stdin is None:
            return
        while True:
            chunk = self._queue.get()
            if chunk is _SENTINEL:
                break
            try:
                stdin.write(chunk.tobytes())
                stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return  # process dead (abort) — stop writing
        try:
            stdin.close()  # EOF → paplay drains and exits
        except OSError:
            pass

    def _stop_process(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._sample_rate = None
            self._drain_queue()
        self._queue.put(_SENTINEL)  # unblocks the writer if it's stuck on get
        if proc is None:
            return
        self._kill(proc)

    @staticmethod
    def _kill(proc: subprocess.Popen[bytes]) -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        except OSError as exc:
            print(f"[PaplayPlayer] failed to terminate paplay: {exc}", file=sys.stderr)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
