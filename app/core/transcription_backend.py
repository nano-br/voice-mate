from __future__ import annotations

from typing import Protocol

import numpy as np
from numpy.typing import NDArray


class TranscriptionBackend(Protocol):
    """Motor de transcrição plugável.

    Implementações concretas (faster-whisper para NVIDIA/CPU, openai-whisper
    sobre torch+ROCm para AMD, etc.) ficam isoladas e são trocadas pela factory
    `cli.wiring.build_transcriber` sem mexer no fluxo de gravação.
    """

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        """Retorna o texto transcrito, ou string vazia se nada foi detectado."""
        ...
