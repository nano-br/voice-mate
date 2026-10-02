from __future__ import annotations

from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class TranscriptionBackend(Protocol):
    """Pluggable transcription engine.

    Concrete implementations (faster-whisper for NVIDIA/CPU, openai-whisper
    over torch+ROCm for AMD, etc.) stay isolated and are swapped by the factory
    `cli.wiring.build_transcriber` without touching the recording flow.
    """

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        """Return the transcribed text, or an empty string if nothing was detected."""
        ...
