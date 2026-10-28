import os

import numpy as np
from faster_whisper import WhisperModel
from numpy.typing import NDArray

from app.i18n import _
from app.protocol.models import SttBackendName, SttDevice

# Ceiling for the automatic CPU thread count. Measured on a 6P+4E i5-13400F (16
# logical CPUs): 4 and 8 threads transcribe at about the same speed and 12 is ~35%
# SLOWER (the extra threads land on E-cores and hyperthreads). Staying at or below
# half the logical CPUs also leaves the rest of the machine responsive.
_MAX_AUTO_CPU_THREADS = 8


def default_cpu_threads() -> int:
    """Threads for faster-whisper on the CPU: half the logical CPUs, at most 8."""
    return max(1, min(_MAX_AUTO_CPU_THREADS, (os.cpu_count() or 2) // 2))


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

    On the CPU `vad_filter` drops silence before decoding: Whisper always encodes a
    30 s window, so a silent tail costs a full pass and tends to come back as an
    invented subtitle line ("Legenda por ..."); trimming it is faster and cleaner.
    """

    name: SttBackendName = "faster-whisper"

    def __init__(
        self,
        model_size: str,
        use_cpu: bool,
        beam_size: int,
        language: str | None = None,
        cpu_threads: int = 0,
        vad_filter: bool = False,
    ) -> None:
        device = "cpu" if use_cpu else "cuda"
        compute_type = "int8" if use_cpu else "int8_float16"
        self.device: SttDevice = "cpu" if use_cpu else "gpu"
        self.model_name = model_size
        threads = (cpu_threads or default_cpu_threads()) if use_cpu else 0
        print(
            _("[VoiceMate] Loading Whisper '{model_size}' on {device} ({compute_type})...").format(
                model_size=model_size, device=device.upper(), compute_type=compute_type
            )
        )
        print(_("[VoiceMate] (first run downloads the model automatically)"))
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type, cpu_threads=threads)
        self._beam_size = beam_size
        self._language = language
        self._vad_filter = vad_filter
        print(_("[VoiceMate] Model ready."))

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        """Return the transcribed text, or an empty string if no speech was detected."""
        segments, _ = self._model.transcribe(
            audio, beam_size=self._beam_size, language=self._language, vad_filter=self._vad_filter
        )
        return " ".join(seg.text for seg in segments).strip()

    def warmup(self) -> None:
        """Run 1 s of silence once so the first real dictation doesn't pay the first-call setup."""
        try:
            segments, _ = self._model.transcribe(
                np.zeros(16000, dtype=np.float32), beam_size=1, language=self._language
            )
            list(segments)  # decoding is lazy: consume it so the pass actually runs
        except Exception:  # noqa: BLE001 (warmup is best-effort)
            pass


# Back-compat: code/tests that import `Transcriber` keep working.
Transcriber = FasterWhisperBackend
