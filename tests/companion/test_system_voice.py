"""Windows' own voices (`app.companion.win.system_voice`), against the real SAPI of this machine."""

from __future__ import annotations

import sys

import pytest

from app.companion.voice import LOCAL_URL, LOCAL_VOICE_PREFIX
from app.companion.win import system_voice
from app.protocol.tts_provider import FORMAT_S16, ProviderError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows speech only")


@pytest.fixture
def voice() -> system_voice.SystemVoice:
    speaker = system_voice.SystemVoice()
    try:
        speaker.info()
    except ProviderError as exc:  # a stripped Windows without any voice
        pytest.skip(exc.detail)
    return speaker


def test_it_is_the_local_voice_of_the_assistant() -> None:
    assert system_voice.URL == LOCAL_URL
    assert system_voice.VOICE_PREFIX == LOCAL_VOICE_PREFIX


def test_the_voices_of_windows_are_listed_with_their_language(voice: system_voice.SystemVoice) -> None:
    info = voice.info()
    assert info.name == "Windows"
    assert info.voices
    assert all(entry.id.startswith(LOCAL_VOICE_PREFIX) for entry in info.voices)
    assert sum(entry.default for entry in info.voices) <= 1
    assert any(entry.languages for entry in info.voices)  # "pt-BR", "en-US"...


def test_a_line_comes_out_as_pcm_in_small_chunks(voice: system_voice.SystemVoice) -> None:
    first = voice.info().voices[0]
    stream = voice.speak("Hello there.", voice=first.id, audio_format=FORMAT_S16, request_id="r1")
    assert (stream.sample_rate, stream.channels, stream.audio_format) == (system_voice.SAMPLE_RATE, 1, FORMAT_S16)
    chunks = list(stream.chunks())
    assert len(chunks) > 1
    assert sum(len(chunk) for chunk in chunks) > system_voice.SAMPLE_RATE  # well over half a second
    assert all(len(chunk) % 2 == 0 for chunk in chunks)


def test_a_cancel_or_a_close_ends_the_stream(voice: system_voice.SystemVoice) -> None:
    stream = voice.speak("A sentence long enough for a few chunks of audio.", audio_format=FORMAT_S16, request_id="r2")
    chunks = stream.chunks()
    next(chunks)
    voice.cancel("r2")
    assert list(chunks) == []
    stream.close()


def test_an_unknown_voice_is_named_as_such(voice: system_voice.SystemVoice) -> None:
    with pytest.raises(ProviderError) as caught:
        voice.speak("Hi.", voice="windows/NoSuchVoice", audio_format=FORMAT_S16, request_id="r3")
    assert caught.value.code == "unknown_voice"
