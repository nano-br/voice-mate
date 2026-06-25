import numpy as np
from faster_whisper import WhisperModel
from numpy.typing import NDArray

from app.i18n import _


class FasterWhisperBackend:
    """Transcribe audio using faster-whisper (CTranslate2 backend).

    Covers NVIDIA (device="cuda", int8_float16) and CPU (int8). On AMD it only
    works when the installed CTranslate2 is the ROCm fork (HIP reports itself as
    "cuda"); the factory `cli.wiring.build_transcriber` decides this via
    `ct2_rocm_ok` and falls back to whisper.cpp/openai-whisper otherwise. Satisfies
    the `core.transcription_backend.TranscriptionBackend` Protocol.

    `language=None` lets Whisper detect the language per utterance; pinning (e.g. "pt")
    gives stability — it stops the detector from classifying a short utterance in the
    wrong language (code-switching of foreign terms still works).
    """

    def __init__(self, model_size: str, use_cpu: bool, beam_size: int, language: str | None = None) -> None:
        device = "cpu" if use_cpu else "cuda"
        compute_type = "int8" if use_cpu else "int8_float16"
        print(
            _("[VoiceMate] Loading Whisper '{model_size}' on {device} ({compute_type})...").format(
                model_size=model_size, device=device.upper(), compute_type=compute_type
            )
        )
        print(_("[VoiceMate] (first run downloads the model automatically)"))
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._beam_size = beam_size
        self._language = language
        print(_("[VoiceMate] Model ready."))

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        """Return the transcribed text, or an empty string if no speech was detected."""
        segments, _ = self._model.transcribe(audio, beam_size=self._beam_size, language=self._language)
        return " ".join(seg.text for seg in segments).strip()


# Back-compat: code/tests that import `Transcriber` keep working.
Transcriber = FasterWhisperBackend
