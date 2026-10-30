"""Windows' own voices for the assistant, so it speaks with nothing else set up.

Windows ships text to speech voices for every language pack installed (the "Speech" voices of
Settings, Time and language): the classic SAPI 5 desktop voices and the newer OneCore ones. Both
are reached through SAPI (`SAPI.SpVoice`, pywin32's late-bound COM): the OneCore voices by
pointing a token category at their registry key. A line is synthesized into memory as 16-bit
PCM, then handed out in small chunks, the same shape as a `voicemate-tts` stream, so the
assistant's voice (`app.companion.voice`) plays, pauses and stops it like the engine's.

Voice ids are `windows/<token name>` (`windows/MSTTS_V110_ptBR_MariaM`), so a saved id says which
source it belongs to. "" is the voice chosen in Windows' own settings. Windows only.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Final

from app.protocol.tts_provider import FORMAT_S16, ProviderError, ProviderInfo, Voice

log = logging.getLogger(__name__)

URL: Final = "windows:"
NAME: Final = "Windows"
VOICE_PREFIX: Final = "windows/"
SAMPLE_RATE: Final = 22050
_SAFT_22KHZ_16BIT_MONO: Final = 22  # SpeechAudioFormatType
_SVSF_IS_NOT_XML: Final = 16  # SpeechVoiceSpeakFlags: a reply starting with "<" is read, not parsed
_ONECORE_VOICES: Final = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
_CHUNK: Final = SAMPLE_RATE * 2 // 5  # 0.2 s: a hold or a stop is noticed between chunks
_LOCALE_NAME_MAX: Final = 85


def is_system_voice(voice: str) -> bool:
    return voice.startswith(VOICE_PREFIX)


def available() -> bool:
    return sys.platform == "win32"


@dataclass(frozen=True)
class _Token:
    id: str  # windows/<token name>
    name: str
    language: str  # BCP 47, "" when Windows does not say
    token: Any


def _com() -> Any:  # noqa: ANN401 - late-bound COM objects have no static type
    """pywin32's COM client, ready on this thread (each thread has its own COM apartment)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()  # once per thread; a second call is harmless
    return win32com.client


def _language(lcid_hex: str) -> str:
    """SAPI's "Language" attribute (hex LCIDs, "416;416") -> "pt-BR"."""
    try:
        lcid = int(lcid_hex.split(";")[0], 16)
    except ValueError:
        return ""
    buffer = ctypes.create_unicode_buffer(_LOCALE_NAME_MAX)
    if not ctypes.windll.kernel32.LCIDToLocaleName(lcid, buffer, _LOCALE_NAME_MAX, 0):  # type: ignore[attr-defined]
        return ""
    return buffer.value


def _tokens(client: Any, speaker: Any) -> list[_Token]:  # noqa: ANN401 - late-bound COM
    """OneCore voices first (newer, more languages), then the desktop ones not already there."""
    found: list[_Token] = []
    seen: set[str] = set()
    collections: list[Any] = []
    try:
        category = client.Dispatch("SAPI.SpObjectTokenCategory")
        category.SetId(_ONECORE_VOICES, False)
        collections.append(category.EnumerateTokens())
    except Exception:  # noqa: BLE001 - older Windows: no OneCore voices, the desktop ones remain
        log.info("system voice: no OneCore voices", exc_info=True)
    collections.append(speaker.GetVoices())
    for tokens in collections:
        for index in range(tokens.Count):
            token = tokens.Item(index)
            name = str(token.GetDescription())
            if name in seen:
                continue
            seen.add(name)
            key = str(token.Id).rsplit("\\", 1)[-1]
            try:
                language = _language(str(token.GetAttribute("Language")))
            except Exception:  # noqa: BLE001 - a voice without the attribute is still usable
                language = ""
            found.append(_Token(VOICE_PREFIX + key, name, language, token))
    return found


class SystemVoice:
    """The voices of Windows behind the calls `EngineVoice` makes to a provider client."""

    url = URL

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()

    def info(self) -> ProviderInfo:
        client = _com()
        try:
            speaker = client.Dispatch("SAPI.SpVoice")
            default = str(speaker.Voice.GetDescription())
            tokens = _tokens(client, speaker)
        except Exception as exc:  # noqa: BLE001 - COM raises its own error types
            raise ProviderError("not_ready", f"Windows speech is not available: {exc}") from exc
        if not tokens:
            raise ProviderError("not_ready", "Windows has no text to speech voice installed")
        voices = tuple(
            Voice(token.id, token.name, (token.language,) if token.language else (), token.name == default)
            for token in tokens
        )
        return ProviderInfo(NAME, voices, SAMPLE_RATE, (FORMAT_S16,), None, ())

    def health(self) -> str:
        return "ok"

    def warmup(self) -> None:
        return None

    def cancel(self, request_id: str) -> None:
        with self._lock:
            self._cancelled.add(request_id)

    def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
        audio_format: str = FORMAT_S16,
        request_id: str | None = None,
        default_rate: int = SAMPLE_RATE,
    ) -> SystemSpeech:
        """Synthesize `text` now (a sentence takes milliseconds) and return it as a stream."""
        del language, default_rate  # the voice decides the language; the rate is ours
        if audio_format != FORMAT_S16:
            raise ProviderError("rejected", f"Windows speech only gives {FORMAT_S16}")
        client = _com()
        try:
            speaker = client.Dispatch("SAPI.SpVoice")
            if voice:
                match = next((token for token in _tokens(client, speaker) if token.id == voice), None)
                if match is None:
                    raise ProviderError("rejected", f"no Windows voice {voice!r}", code="unknown_voice")
                speaker.Voice = match.token
            audio_format_object = client.Dispatch("SAPI.SpAudioFormat")
            audio_format_object.Type = _SAFT_22KHZ_16BIT_MONO
            memory = client.Dispatch("SAPI.SpMemoryStream")
            memory.Format = audio_format_object
            speaker.AudioOutputStream = memory
            speaker.Speak(text, _SVSF_IS_NOT_XML)  # synchronous, into memory
            pcm = bytes(memory.GetData())
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - COM raises its own error types
            raise ProviderError("server", f"Windows speech failed: {exc}") from exc
        return SystemSpeech(self, request_id or uuid.uuid4().hex, pcm)

    def _was_cancelled(self, request_id: str) -> bool:
        with self._lock:
            return request_id in self._cancelled

    def _forget(self, request_id: str) -> None:
        with self._lock:
            self._cancelled.discard(request_id)


class SystemSpeech:
    """One synthesized line, handed out like a provider's audio stream."""

    sample_rate = SAMPLE_RATE
    channels = 1
    audio_format = FORMAT_S16

    def __init__(self, owner: SystemVoice, request_id: str, pcm: bytes) -> None:
        self._owner = owner
        self.request_id = request_id
        self._pcm = pcm
        self._closed = threading.Event()

    def chunks(self, size: int = _CHUNK) -> Iterator[bytes]:
        for start in range(0, len(self._pcm), size):
            if self._closed.is_set() or self._owner._was_cancelled(self.request_id):
                return
            yield self._pcm[start : start + size]

    def close(self) -> None:
        self._closed.set()
        self._owner._forget(self.request_id)
