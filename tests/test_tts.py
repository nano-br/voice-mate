from __future__ import annotations

from app.features.tts.base import NullSpeaker


def test_null_speaker_is_not_active() -> None:
    speaker = NullSpeaker()
    assert speaker.is_active() is False


def test_null_speaker_methods_are_noop() -> None:
    speaker = NullSpeaker()
    # Não devem levantar nem retornar nada significativo.
    speaker.speak("qualquer coisa")
    speaker.stop()
    speaker.close()
