"""GPU Whisper via openai-whisper (opt-in extra: `voice-mate[whisper-gpu]`).

Exists for AMD: faster-whisper (CTranslate2) doesn't accelerate on ROCm, but
openai-whisper runs on the SAME torch+ROCm that VoxCPM uses — on the AMD card
torch reports device "cuda" (HIP). It also works on NVIDIA, although there
faster-whisper is preferred for being faster/lighter.

Requires the `openai-whisper` package. Call `is_available()` before instantiating.
"""

from __future__ import annotations

from app.core.config import Config
from app.core.transcription_backend import TranscriptionBackend

__all__ = ["build_backend", "is_available"]


def is_available() -> bool:
    """Return True if the `openai-whisper` extra is installed."""
    try:
        import whisper  # noqa: F401 — openai-whisper package, imported as `whisper`
    except ImportError:
        return False
    return True


def build_backend(config: Config) -> TranscriptionBackend:
    """Instantiate the openai-whisper backend (lazy import of the heavy package)."""
    from app.features.openai_whisper.backend import OpenAIWhisperBackend

    return OpenAIWhisperBackend(config)
