from __future__ import annotations

import queue
import sys
import threading
from typing import Any

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray


class AudioPlayer:
    """Player de áudio com fila para chunks float32 mono.

    Reproduz em tempo real chunks enviados via `feed()`. `drain()` bloqueia
    até a fila esvaziar; `abort()` interrompe imediatamente (descarta a fila
    e aborta o buffer do driver).
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[NDArray[np.float32]] = queue.Queue()
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self._aborted = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._leftover: NDArray[np.float32] | None = None

    def start(self, sample_rate: int) -> None:
        with self._lock:
            if self._stream is not None:
                return
            self._aborted.clear()
            self._idle.set()
            self._leftover = None
            self._stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()

    def feed(self, chunk: NDArray[np.float32]) -> None:
        if self._aborted.is_set():
            return
        if chunk.ndim == 1:
            chunk = chunk.reshape(-1, 1)
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)
        self._idle.clear()
        self._queue.put(chunk)

    def drain(self, timeout: float | None = None) -> bool:
        """Bloqueia até a fila esvaziar (ou `abort()`). Retorna True se idle."""
        return self._idle.wait(timeout=timeout)

    def abort(self) -> None:
        """Interrompe imediatamente, descartando buffer interno e do driver."""
        self._aborted.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._leftover = None
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.abort()
                except Exception as exc:  # noqa: BLE001
                    print(f"[AudioPlayer] abort falhou: {exc}", file=sys.stderr)
        self._idle.set()

    def close(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            stream = self._stream
            self._stream = None
        try:
            stream.stop()
            stream.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[AudioPlayer] close falhou: {exc}", file=sys.stderr)

    def _callback(
        self,
        outdata: NDArray[np.float32],
        frames: int,
        time_info: Any,  # noqa: ANN401
        status: sd.CallbackFlags,
    ) -> None:
        if status:
            print(f"[AudioPlayer] {status}", file=sys.stderr)
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
