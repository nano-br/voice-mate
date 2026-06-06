from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config


class OpenAIWhisperBackend:
    """Transcreve com openai-whisper sobre torch (CUDA/HIP-ROCm ou CPU).

    Satisfaz o Protocol `core.transcription_backend.TranscriptionBackend`.
    Se a GPU for pedida mas o torch não tiver CUDA/ROCm disponível, levanta
    RuntimeError — a factory `cli.wiring.build_transcriber` captura e cai para
    o faster-whisper em CPU (mais rápido que openai-whisper em CPU).
    """

    def __init__(self, config: Config) -> None:
        import whisper  # openai-whisper

        device = self._resolve_device(config)
        self._beam_size = config.beam_size
        self._fp16 = device == "cuda"
        print(f"[VoiceMate] Carregando Whisper '{config.model_size}' (openai-whisper) em {device.upper()}...")
        print("[VoiceMate] (primeira execução baixa o modelo automaticamente)")
        self._model = whisper.load_model(config.model_size, device=device)
        print("[VoiceMate] Modelo pronto.")

    @staticmethod
    def _resolve_device(config: Config) -> str:
        if config.use_cpu or config.gpu_vendor == "cpu":
            return "cpu"
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("openai-whisper precisa de torch instalado.") from exc
        # Em ROCm o torch reporta CUDA disponível (HIP se disfarça de cuda).
        if not torch.cuda.is_available():
            raise RuntimeError(
                "Backend GPU pedido, mas torch.cuda.is_available() é False "
                "(driver ROCm/CUDA ausente ou torch CPU-only instalado)."
            )
        return "cuda"

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        result: dict[str, Any] = self._model.transcribe(
            audio,
            beam_size=self._beam_size,
            fp16=self._fp16,
            verbose=False,
        )
        text = result.get("text", "")
        return text.strip() if isinstance(text, str) else ""
