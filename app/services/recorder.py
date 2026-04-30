import sys
import threading
from typing import Any

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray


class Recorder:
    """Captura áudio do microfone em modo toggle (start/stop)."""

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
        """Inicia a gravação. Retorna False se já estava gravando."""
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
        """Para a gravação e retorna o áudio capturado, ou None se vazio."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
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
        """Callback do sounddevice — chamado automaticamente a cada chunk."""
        if status:
            print(f"[recorder] {status}", file=sys.stderr)
        with self._lock:
            self._chunks.append(indata.copy())
