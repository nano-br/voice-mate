"""Text-to-speech feature (opt-in extra: `voice-mate[tts]`).

`base.py` (Protocol + NullSpeaker) is always importable. Concrete engines
(`voxcpm_speaker.py`) require the `voxcpm` and `soundfile` packages —
import them lazily from `build_default_speaker()`.
"""

from __future__ import annotations

from app.core.config import TTSConfig
from app.features.tts.base import NullSpeaker, TextToSpeech

__all__ = ["NullSpeaker", "TextToSpeech", "build_default_speaker", "is_available"]


def is_available() -> bool:
    """Return True if the TTS extras (`voxcpm` + `soundfile`) are installed."""
    try:
        import soundfile  # noqa: F401
        import voxcpm  # noqa: F401
    except ImportError:
        return False
    return True


def build_default_speaker(config: TTSConfig) -> TextToSpeech:
    """Return a concrete speaker if possible, else a NullSpeaker.

    Falls back silently to NullSpeaker when TTS is disabled in config,
    when the engine is "none", or when the extras are not installed.
    """
    if not config.enabled or config.engine == "none" or not is_available():
        return NullSpeaker()
    from app.features.tts.voxcpm_speaker import VoxCPMSpeaker

    return VoxCPMSpeaker(config)
