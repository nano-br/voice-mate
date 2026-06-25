import sys
import threading
from typing import Any

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray


class Recorder:
    """Capture microphone audio in toggle mode (start/stop)."""

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._recording = False
        self._chunks: list[NDArray[np.float32]] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> bool:
        """Start recording. Returns False if already recording."""
        with self._lock:
            if self._recording:
                return False
            self._recording = True
            self._chunks = []
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        return True

    def stop(self) -> NDArray[np.float32] | None:
        """Stop recording and return the captured audio, or None if empty."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            stream = self._stream
            self._stream = None

        # stream.stop() OUTSIDE the lock: PortAudio blocks until the in-flight
        # callback returns, and _callback needs the same lock to append the
        # chunk — holding it here deadlocked the toggle (visible on WSLg, where
        # the PulseAudio-RDP callbacks are slow/frequent).
        if stream is not None:
            stream.stop()
            stream.close()

        with self._lock:
            chunks = list(self._chunks)
            self._chunks = []

        if not chunks:
            return None

        return np.concatenate(chunks).flatten().astype(np.float32)

    def _callback(
        self,
        indata: NDArray[np.float32],
        frames: int,  # noqa: ANN001
        time_info: Any,  # noqa: ANN401
        status: sd.CallbackFlags,
    ) -> None:
        """sounddevice callback — invoked automatically for each chunk."""
        if status:
            print(f"[recorder] {status}", file=sys.stderr)
        with self._lock:
            self._chunks.append(indata.copy())
