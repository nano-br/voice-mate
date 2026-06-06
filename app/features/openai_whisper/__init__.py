"""GPU Whisper via openai-whisper (opt-in extra: `voice-mate[whisper-gpu]`).

Existe para a AMD: o faster-whisper (CTranslate2) não acelera em ROCm, mas o
openai-whisper roda sobre o MESMO torch+ROCm que o VoxCPM usa — na placa AMD o
torch reporta device "cuda" (HIP). Também funciona em NVIDIA, embora lá o
faster-whisper seja preferido por ser mais rápido/leve.

Requer o pacote `openai-whisper`. Chame `is_available()` antes de instanciar.
"""

from __future__ import annotations

from app.core.config import Config
from app.core.transcription_backend import TranscriptionBackend

__all__ = ["build_backend", "is_available"]


def is_available() -> bool:
    """Return True if the `openai-whisper` extra is installed."""
    try:
        import whisper  # noqa: F401 — pacote openai-whisper, importa como `whisper`
    except ImportError:
        return False
    return True


def build_backend(config: Config) -> TranscriptionBackend:
    """Instancia o backend openai-whisper (import preguiçoso do pacote pesado)."""
    from app.features.openai_whisper.backend import OpenAIWhisperBackend

    return OpenAIWhisperBackend(config)
