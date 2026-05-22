import numpy as np
from faster_whisper import WhisperModel
from numpy.typing import NDArray


class Transcriber:
    """Transcreve áudio usando faster-whisper (CTranslate2 backend)."""

    def __init__(self, model_size: str, use_cpu: bool, beam_size: int) -> None:
        device = "cpu" if use_cpu else "cuda"
        compute_type = "int8" if use_cpu else "int8_float16"
        print(f"[VoiceMate] Carregando Whisper '{model_size}' em {device.upper()} ({compute_type})...")
        print("[VoiceMate] (primeira execução baixa o modelo automaticamente)")
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._beam_size = beam_size
        print("[VoiceMate] Modelo pronto.")

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        """Retorna o texto transcrito, ou string vazia se nenhuma fala detectada."""
        segments, _ = self._model.transcribe(audio, beam_size=self._beam_size)
        return " ".join(seg.text for seg in segments).strip()
