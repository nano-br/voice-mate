"""The spoken-answers settings -> engine flags, and talking to a TTS provider from the settings window.

With the "external" voice engine the engine this app starts is given `--tts-engine external
--tts-url ... --tts-external-voice ...` (docs/tts-provider-protocol.md). The token never goes on
the command line (any local user could read it with `ps`): it travels in the
`VOICEMATE_TTS_TOKEN` environment variable, which `wsl.exe` forwards when `WSLENV` lists it.

The voice picker and the Test button talk to the provider directly from this computer, through
the shared client in `app.protocol.tts_provider`. No Qt here: the controller and the tests
share it.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
import wave
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from app.companion.contract import CompanionSettings, TtsChoice, TtsProbe
from app.i18n import _
from app.protocol.tts_provider import FORMAT_S16, TOKEN_ENV, ProviderClient, ProviderError, SpeechStream

log = logging.getLogger(__name__)

# A test sentence is a few seconds; this stops a provider that never ends from filling memory.
MAX_TEST_SECONDS: Final = 30.0
TEST_FILE_PREFIX: Final = "tts-test-"
TEST_FILE_MAX_AGE_S: Final = 600.0


@dataclass(frozen=True)
class EngineTts:
    """What goes on the engine command line and in its environment."""

    engine: TtsChoice
    url: str = ""
    voice: str = ""
    token: str = field(default="", repr=False)

    def cli_args(self) -> str:
        # "default" leaves the engine's own choice (its saved setup).
        if self.engine == "default":
            return ""
        # `=` joins flag and value so a voice id that starts with "-" is not read as another flag.
        args = f"--tts-engine external --tts-url={self.url}"
        return f"{args} --tts-external-voice={self.voice}" if self.voice else args

    def env(self) -> dict[str, str]:
        return {TOKEN_ENV: self.token} if self.engine == "external" and self.token else {}


def engine_tts(settings: CompanionSettings) -> EngineTts:
    """The flags for these settings. With the default engine the address, voice and token do not
    count (changing them must not restart the engine)."""
    if settings.tts_engine == "default":
        return EngineTts("default")
    return EngineTts("external", settings.tts_url, settings.tts_voice, settings.tts_token)


def spawn_env(tts: EngineTts | None, *, wsl: bool, base: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """The environment of the engine process, or None to inherit ours unchanged. For WSL the
    variable is also listed in `WSLENV`, or `wsl.exe` would not hand it to the Linux side."""
    extra = tts.env() if tts is not None else {}
    if not extra:
        return None
    env = dict(os.environ if base is None else base)
    env.update(extra)
    if wsl:
        listed = [name for name in env.get("WSLENV", "").split(":") if name]
        for name in extra:
            if name not in listed:
                listed.append(name)
        env["WSLENV"] = ":".join(listed)
    return env


# --- talking to the provider ------------------------------------------------------------------------


def explain(error: ProviderError, url: str, voice: str = "") -> str:
    """A localized sentence for a provider problem."""
    if error.kind == "unreachable":
        return _("Could not reach the provider at {url}. Is it running?").format(url=url)
    if error.kind == "unauthorized":
        return _("The provider refused the token. Check the Token field.")
    if error.kind == "protocol":
        return _("That address is not a compatible VoiceMate TTS provider: {detail}").format(detail=error.detail)
    if error.kind == "busy":
        return _("The provider is busy. Try again in a moment.")
    if error.kind == "not_ready":
        return _("The provider is still loading its models. Try again in a moment.")
    if error.code == "unknown_voice":
        return _("The provider does not know the voice '{voice}'. Load the voices and pick one.").format(voice=voice)
    if error.kind == "rejected":
        return _("The provider refused the request: {detail}").format(detail=error.detail)
    return _("The provider failed: {detail}").format(detail=error.detail)


def _client(settings: CompanionSettings) -> ProviderClient:
    return ProviderClient(settings.tts_url, settings.tts_token or None)


def probe_voices(settings: CompanionSettings) -> TtsProbe:
    """Ask the provider who it is and which voices it has."""
    try:
        info = _client(settings).info()
    except ValueError:
        return TtsProbe(False, _("The provider address must look like http://127.0.0.1:8765."))
    except ProviderError as exc:
        log.info("TTS provider probe failed: %s", exc.detail)
        return TtsProbe(False, explain(exc, settings.tts_url, settings.tts_voice))
    return TtsProbe(
        True,
        _("Connected to {name}. Voices found: {count}.").format(name=info.name, count=len(info.voices)),
        info.name,
        info.voices,
    )


def try_voice(settings: CompanionSettings, directory: Path, play: Callable[[Path], None]) -> TtsProbe:
    """Have the provider speak a short sentence with the chosen voice, then play it.

    The audio is written as a .wav next to the sound cues (the platform players take files)
    and played when it is complete: a test sentence is short, and one file keeps the
    platform code the same as for cues."""
    try:
        client = _client(settings)
    except ValueError:
        return TtsProbe(False, _("The provider address must look like http://127.0.0.1:8765."))
    voice = settings.tts_voice
    try:
        info = client.info()
        if client.health() == "loading":
            return TtsProbe(False, explain(ProviderError("not_ready", "loading"), settings.tts_url), info.name)
        started = time.monotonic()
        stream = client.speak(
            _("Hello! This is how VoiceMate sounds with this voice."),
            voice=voice or None,
            audio_format=FORMAT_S16,
            default_rate=info.sample_rate,
        )
        audio = bytearray()
        try:
            if stream.audio_format != FORMAT_S16:  # the .wav below is written as signed 16-bit
                raise ProviderError("protocol", f"asked for {FORMAT_S16}, got {stream.audio_format}")
            limit = int(MAX_TEST_SECONDS * stream.sample_rate * stream.channels * 2)
            for chunk in stream.chunks():
                audio += chunk
                if len(audio) >= limit:
                    break
        finally:
            stream.close()
    except ProviderError as exc:
        log.info("TTS provider test failed: %s", exc.detail)
        return TtsProbe(False, explain(exc, settings.tts_url, voice))
    if not audio:
        return TtsProbe(False, _("The provider sent no audio."), info.name, info.voices)
    path = _write_wav(directory, bytes(audio[: len(audio) - len(audio) % (2 * stream.channels)]), stream)
    play(path)
    log.info("TTS provider test: %.0f ms until done, %d bytes", (time.monotonic() - started) * 1000, len(audio))
    return TtsProbe(True, _("Playing a test sentence from {name}.").format(name=info.name), info.name, info.voices)


def _write_wav(directory: Path, pcm: bytes, stream: SpeechStream) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    _remove_old_tests(directory)
    # A new name each time: a sound still playing keeps its old file open (like the cues).
    path = directory / f"{TEST_FILE_PREFIX}{uuid.uuid4().hex[:12]}.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(stream.channels)
        wav.setsampwidth(2)
        wav.setframerate(stream.sample_rate)
        wav.writeframes(pcm)
    return path


def _remove_old_tests(directory: Path) -> None:
    now = time.time()
    for old in directory.glob(f"{TEST_FILE_PREFIX}*.wav"):
        try:
            if now - old.stat().st_mtime > TEST_FILE_MAX_AGE_S:
                old.unlink()
        except OSError:
            pass  # still playing, or gone: the next test cleans it
