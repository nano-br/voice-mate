from __future__ import annotations

import sys
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config
from app.i18n import _

_WHISPER_SAMPLE_RATE = 16000
# Above this, it's worth running VAD to trim silence (long audio). Below, the
# audio already transcribes sub-second and VAD would only add latency.
_VAD_MIN_SAMPLES = 20 * _WHISPER_SAMPLE_RATE


class OpenAIWhisperBackend:
    """Transcribes with openai-whisper on top of torch (CUDA/HIP-ROCm or CPU).

    Satisfies the `core.transcription_backend.TranscriptionBackend` Protocol.
    If the GPU is requested but torch has no CUDA/ROCm available, it raises
    RuntimeError — the `cli.wiring.build_transcriber` factory catches it and
    falls back to faster-whisper on CPU (faster than openai-whisper on CPU).
    """

    def __init__(self, config: Config) -> None:
        import whisper  # openai-whisper

        device = self._resolve_device(config)
        self._beam_size = config.beam_size
        self._fp16 = device == "cuda"
        # Fixed language (≠"auto"): stability + skips spoken-language detection
        # (same rule as the other backends; code-switching still works).
        self._language: str | None = None if config.transcription_language == "auto" else config.transcription_language
        print(
            _("[VoiceMate] Loading Whisper '{model_size}' (openai-whisper) on {device}...").format(
                model_size=config.model_size, device=device.upper()
            )
        )
        print(_("[VoiceMate] (first run downloads the model automatically)"))
        self._model = whisper.load_model(config.model_size, device=device)
        print(_("[VoiceMate] Model ready."))

    @staticmethod
    def _resolve_device(config: Config) -> str:
        if config.use_cpu or config.gpu_vendor == "cpu":
            return "cpu"
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("openai-whisper requires torch to be installed.") from exc
        # On ROCm, torch reports CUDA as available (HIP masquerades as cuda).
        if not torch.cuda.is_available():
            raise RuntimeError(
                "GPU backend requested, but torch.cuda.is_available() is False "
                "(ROCm/CUDA driver missing or CPU-only torch installed)."
            )
        return "cuda"

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        # Long audio: trim silence with VAD before transcribing (openai-whisper
        # has no built-in VAD like faster-whisper). Short audio (everyday
        # commands) passes straight through — it's already sub-second.
        if audio.shape[0] > _VAD_MIN_SAMPLES:
            from app.features.openai_whisper.vad import trim_to_speech

            audio = trim_to_speech(audio, _WHISPER_SAMPLE_RATE)
        result: dict[str, Any] = self._model.transcribe(
            audio,
            beam_size=self._beam_size,
            fp16=self._fp16,
            language=self._language,
            # temperature=0.0 disables Whisper's fallback ladder
            # (0.0, 0.2…1.0): by default, when a low-confidence segment "fails"
            # the thresholds (common in real microphone audio), it RE-DECODES the
            # same chunk up to 6×. On long/noisy audio this multiplies the
            # forward passes and pegs the GPU. Pinning to 0.0 keeps decoding
            # deterministic and fast.
            temperature=0.0,
            # condition_on_previous_text=False: prevents the context from growing
            # over long continuous audio (which slows down each 30s window) and
            # reduces the risk of a repetition loop. Prioritizes speed.
            condition_on_previous_text=False,
            verbose=False,
        )
        text = result.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    def warmup(self) -> None:
        """Transcribe 1s of silence to pay the MIOpen kernel search at startup.

        Without this, the 1st real transcription pays the cost (tens of seconds on
        AMD), on top of contending for the GPU with the TTS warmup. Best-effort:
        silent.
        """
        try:
            self.transcribe(np.zeros(16000, dtype=np.float32))
        except Exception as exc:  # noqa: BLE001 — warmup is best-effort
            print(
                _("[VoiceMate] ⚠ openai-whisper warmup failed (continuing): {exc}").format(exc=exc),
                file=sys.stderr,
            )
