"""Text-to-speech feature (opt-in extra: `voice-mate[tts]`).

`base.py` (Protocol + NullSpeaker) is always importable. Concrete engines
require their own packages — `omnivoice_speaker.py` precisa de `omnivoice` +
`soundfile` (engine padrão), `voxcpm_speaker.py` de `voxcpm` + `soundfile`
(alternativo) — importados sob demanda em `build_default_speaker()`.
"""

from __future__ import annotations

from app.core.config import TTSConfig, TTSEngine
from app.features.tts.base import NullSpeaker, TextToSpeech

__all__ = ["NullSpeaker", "TextToSpeech", "build_default_speaker", "is_available"]


def is_available(engine: TTSEngine = "omnivoice") -> bool:
    """True se os pacotes do engine ativo (engine + `soundfile`) estão instalados."""
    try:
        import soundfile  # noqa: F401
    except ImportError:
        return False
    if engine == "omnivoice":
        try:
            import omnivoice  # noqa: F401
        except ImportError:
            return False
        return True
    if engine == "kokoro":
        try:
            import kokoro  # noqa: F401
        except ImportError:
            return False
        return True
    if engine == "voxcpm":
        try:
            import voxcpm  # noqa: F401
        except ImportError:
            return False
        return True
    return False


def build_default_speaker(config: TTSConfig) -> TextToSpeech:
    """Return a concrete speaker if possible, else a NullSpeaker.

    Falls back silently to NullSpeaker when TTS is disabled in config,
    when the engine is "none", or when the engine's extras are not installed.
    """
    if not config.enabled or config.engine == "none" or not is_available(config.engine):
        return NullSpeaker()
    if config.engine == "voxcpm":
        from app.features.tts.voxcpm_speaker import VoxCPMSpeaker

        return VoxCPMSpeaker(config)
    if config.engine == "kokoro":
        from app.features.tts.kokoro_speaker import KokoroSpeaker

        return KokoroSpeaker(config)
    from app.features.tts.omnivoice_speaker import OmniVoiceSpeaker

    return OmniVoiceSpeaker(config)
