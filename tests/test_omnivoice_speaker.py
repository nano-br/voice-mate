"""OmniVoiceSpeaker: montagem dos kwargs de geração (sem carregar o modelo)."""

from __future__ import annotations

from app.core.config import TTSConfig
from app.features.tts.omnivoice_speaker import OmniVoiceSpeaker


def test_generate_kwargs_includes_mapped_language_name() -> None:
    # código do config ("pt") → nome de idioma do OmniVoice ("Portuguese").
    speaker = OmniVoiceSpeaker(TTSConfig(language="pt"))
    kwargs = speaker._generate_kwargs("olá mundo")
    assert kwargs["text"] == "olá mundo"
    assert kwargs["language"] == "Portuguese"


def test_generate_kwargs_auto_omits_language() -> None:
    # "auto" → não passa language (OmniVoice detecta pelo texto).
    speaker = OmniVoiceSpeaker(TTSConfig(language="auto"))
    assert "language" not in speaker._generate_kwargs("olá mundo")


def test_generate_kwargs_off_mode_is_plain_no_clone() -> None:
    # default (voice_seed_mode="off"): plain — sem clonagem (ref) e sem instruct
    # (o voice-design do OmniVoice trava no Windows/ROCm).
    speaker = OmniVoiceSpeaker(TTSConfig(voice_seed_mode="off"))
    kwargs = speaker._generate_kwargs("olá")
    assert "ref_audio" not in kwargs
    assert "ref_text" not in kwargs
    assert "instruct" not in kwargs


def test_finalize_audio_clips_and_fades_edges() -> None:
    import numpy as np

    speaker = OmniVoiceSpeaker(TTSConfig())
    out = speaker._finalize_audio(np.full(2400, 2.0, dtype=np.float32))
    assert out.max() <= 1.0 and out.min() >= -1.0  # clipado em [-1,1]
    assert out[0] == 0.0 and out[-1] == 0.0  # fade-in/out começam/terminam em silêncio
