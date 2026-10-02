from __future__ import annotations

import queue
import sys
import threading
from typing import Any, Protocol

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray

from app.platform.detect import detect_platform


class AudioSink(Protocol):
    """Common interface for the audio players (sounddevice and paplay)."""

    def ensure_started(self, sample_rate: int) -> None: ...

    def start(self, sample_rate: int) -> None: ...

    def feed(self, chunk: NDArray[np.float32]) -> None: ...

    def drain(self, timeout: float | None = 60.0) -> bool: ...

    def abort(self) -> None: ...

    def close(self) -> None: ...


def create_audio_player() -> AudioSink:
    """Pick the player per platform.

    Linux/WSL2 with `paplay`: native PulseAudio (robust on WSLg, without the
    PortAudio-over-RDP deadlock that hung the app). Windows (WASAPI): sounddevice.
    """
    if detect_platform() in ("wsl2", "linux-x11", "linux-wayland"):
        from app.features.tts.paplay_player import PaplayPlayer, paplay_available

        if paplay_available():
            return PaplayPlayer()
    return AudioPlayer()


# Output buffer per platform. On WSLg, audio goes out over PulseAudio-over-RDP,
# which has high jitter: tiny blocks (blocksize=0, ~34 ms) drain the buffer
# mid-speech → underrun → crackle. blocksize=4096 @ 24 kHz (~170 ms) +
# latency=0.2 give ~340 ms effective (measured), enough slack for RDP.
# Windows/WASAPI already works with the default — don't touch it.
_WSL_LINUX_BLOCKSIZE = 4096
_WSL_LINUX_LATENCY = 0.2


def _default_audio_params() -> tuple[int, float | str | None]:
    """(blocksize, latency) per platform. WSL2/Linux → larger buffer."""
    if detect_platform() in ("wsl2", "linux-x11", "linux-wayland"):
        return _WSL_LINUX_BLOCKSIZE, _WSL_LINUX_LATENCY
    return 0, None  # Windows/macOS: PortAudio default


class AudioPlayer:
    """Queue-based audio player for mono float32 chunks.

    Plays in real time the chunks sent via `feed()`. `drain()` blocks until the
    queue empties; `abort()` interrupts immediately (discards the queue and
    aborts the driver buffer).
    """

    def __init__(self, blocksize: int | None = None, latency: float | str | None = None) -> None:
        default_blocksize, default_latency = _default_audio_params()
        self._blocksize = default_blocksize if blocksize is None else blocksize
        self._latency = default_latency if latency is None else latency
        self._queue: queue.Queue[NDArray[np.float32]] = queue.Queue()
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self._aborted = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._leftover: NDArray[np.float32] | None = None
        self._sample_rate: int | None = None
        self._underflow_logged = False

    def ensure_started(self, sample_rate: int) -> None:
        """Ensure an open, compatible stream — opens only if needed.

        Unlike `start()`, it **doesn't reopen** if there's already an active
        stream with the same sample_rate. This keeps ONE persistent stream across
        sentences/turns, eliminating the open/close clicks on every utterance and
        the gaps between utterances.
        """
        with self._lock:
            ready = self._stream is not None and not self._aborted.is_set() and self._sample_rate == sample_rate
        if ready:
            return
        self.start(sample_rate)

    def start(self, sample_rate: int) -> None:
        """Create a new OutputStream — close the old one if it exists.

        Each call spins up a fresh stream, avoiding degradation after heavy use
        or after `abort()`. Clears internal flags so `feed()` accepts chunks
        normally again. Prefer `ensure_started()` on the normal speech path; use
        `start()` to force a new stream.
        """
        with self._lock:
            old = self._stream
            self._stream = None
            self._aborted.clear()
            self._idle.set()
            self._leftover = None
            self._underflow_logged = False
            self._drain_queue()
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._blocksize,
                latency=self._latency,
                callback=self._callback,
            )
            stream.start()
            self._stream = stream
            self._sample_rate = sample_rate
        if old is not None:
            self._close_stream_safely(old)

    def feed(self, chunk: NDArray[np.float32]) -> None:
        if self._aborted.is_set():
            return
        if chunk.ndim == 1:
            chunk = chunk.reshape(-1, 1)
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)
        self._idle.clear()
        self._queue.put(chunk)

    def drain(self, timeout: float | None = 60.0) -> bool:
        """Block until the queue empties (or `abort()`). Returns True if idle.

        Default 60s to avoid hanging forever if the sounddevice callback stops
        running for some reason (driver, etc).
        """
        return self._idle.wait(timeout=timeout)

    def abort(self) -> None:
        """Interrupt immediately, closing the stream and clearing state.

        After `abort()`, the player returns to its initial state — a call to
        `start()` creates a new stream and `feed()` works again.
        """
        self._aborted.set()
        self._drain_queue()
        self._leftover = None
        with self._lock:
            stream = self._stream
            self._stream = None
            self._sample_rate = None
        if stream is not None:
            try:
                stream.abort()
            except Exception as exc:  # noqa: BLE001
                print(f"[AudioPlayer] abort failed: {exc}", file=sys.stderr)
            self._close_stream_safely(stream)
        self._idle.set()

    def close(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            stream = self._stream
            self._stream = None
            self._sample_rate = None
        self._close_stream_safely(stream)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _close_stream_safely(stream: sd.OutputStream) -> None:
        try:
            stream.stop()
            stream.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[AudioPlayer] close failed: {exc}", file=sys.stderr)

    def _callback(
        self,
        outdata: NDArray[np.float32],
        frames: int,
        time_info: Any,  # noqa: ANN401
        status: sd.CallbackFlags,
    ) -> None:
        if status and not self._underflow_logged:
            # Log only the 1st occurrence per stream — before, it flooded the
            # console on every callback. An isolated underflow at the start
            # (queue still filling) is normal.
            print(f"[AudioPlayer] {status}", file=sys.stderr)
            self._underflow_logged = True
        if self._aborted.is_set():
            outdata.fill(0)
            return
        filled = 0
        if self._leftover is not None:
            take = min(frames - filled, self._leftover.shape[0])
            outdata[filled : filled + take, 0] = self._leftover[:take, 0]
            if take == self._leftover.shape[0]:
                self._leftover = None
            else:
                self._leftover = self._leftover[take:]
            filled += take
        while filled < frames:
            try:
                chunk = self._queue.get_nowait()
            except queue.Empty:
                outdata[filled:].fill(0)
                self._idle.set()
                return
            take = min(frames - filled, chunk.shape[0])
            outdata[filled : filled + take, 0] = chunk[:take, 0]
            if take < chunk.shape[0]:
                self._leftover = chunk[take:]
            filled += take
