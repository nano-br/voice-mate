"""Client for the external TTS provider protocol, version 1 (docs/tts-provider-protocol.md).

Standard library only: the engine's speaker, the companion (voice picker and Test button) and
`tools/tts_provider_check.py` all talk to a provider through this one module, and the companion
ships without numpy. It deals in bytes; turning PCM into audio is the caller's job. The URL, the
errors and the plain HTTP calls are shared with the STT protocol (`app.protocol.provider_http`).
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.protocol.provider_http import CONNECT_TIMEOUT_S as CONNECT_TIMEOUT_S
from app.protocol.provider_http import READ_TIMEOUT_S as READ_TIMEOUT_S
from app.protocol.provider_http import Endpoint as Endpoint
from app.protocol.provider_http import ErrorKind as ErrorKind
from app.protocol.provider_http import HttpProvider, describe_os_error, error_from, strings
from app.protocol.provider_http import ProviderError as ProviderError
from app.protocol.provider_http import parse_url as parse_url
from app.protocol.provider_http import url_is_valid as url_is_valid
from app.protocol.provider_http import value_is_safe as value_is_safe

PROTOCOL_NAME: Final = "voicemate-tts"
PROTOCOL_VERSION: Final = 1
DEFAULT_URL: Final = "http://127.0.0.1:8765"
TOKEN_ENV: Final = "VOICEMATE_TTS_TOKEN"
# The command that starts the provider when it does not answer (`app.features.tts.provider_launcher`).
START_COMMAND_ENV: Final = "VOICEMATE_TTS_START_COMMAND"

FORMAT_S16: Final = "pcm_s16le"
FORMAT_F32: Final = "pcm_f32le"
SPEAK_FORMATS: Final = (FORMAT_S16, FORMAT_F32)
FEATURE_STREAMING: Final = "streaming"
FEATURE_EXPRESSION_TAGS: Final = "expression_tags"

CANCEL_TIMEOUT_S: Final = 1.5


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    languages: tuple[str, ...] = ()
    default: bool = False


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    voices: tuple[Voice, ...]
    sample_rate: int
    formats: tuple[str, ...]
    max_chars: int | None
    features: tuple[str, ...]

    def supports(self, feature: str) -> bool:
        return feature in self.features

    @property
    def speak_format(self) -> str:
        """The format VoiceMate asks for: signed 16-bit unless the provider lacks it."""
        return FORMAT_S16 if FORMAT_S16 in self.formats else FORMAT_F32


def language_for_provider(language: str) -> str | None:
    """The engine's short language code -> the BCP-47 tag sent to a provider (None = unknown)."""
    return {"pt": "pt-BR", "zh": "zh-CN"}.get(language, None if language == "auto" else language)


_TAGS: Final = re.compile(r"\s*\[/?(?:laugh|chuckle|sigh|pause|whisper)\]\s*", re.IGNORECASE)


def strip_expression_tags(text: str) -> str:
    """Remove `[laugh]`, `[chuckle]`, `[sigh]`, `[pause]` and `[whisper]...[/whisper]` markers."""
    return _TAGS.sub(" ", text).strip()


def split_text(text: str, limit: int) -> list[str]:
    """Pieces of at most `limit` characters, cut at the last sentence end, then space, before it."""
    pieces: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(mark) for mark in (". ", "! ", "? ", "\n")) + 1
        if cut <= 0:
            cut = window.rfind(" ") + 1
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


class SpeechStream:
    """An open `/v1/speak` response: PCM bytes as the provider produces them."""

    def __init__(
        self,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
        request_id: str,
        sample_rate: int,
        channels: int,
        audio_format: str,
        raw_sock: socket.socket | None = None,
    ) -> None:
        self._connection = connection
        self._response = response
        self.request_id = request_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_format = audio_format
        self._closed = threading.Event()
        # `getresponse()` clears `connection.sock` when the body ends with the connection
        # (HTTP/1.0 or `Connection: close`), so `speak()` hands over the socket it saw right
        # after `request()`, for `close()` to shut down.
        self._raw_sock = raw_sock or connection.sock

    def chunks(self, size: int = 16384) -> Iterator[bytes]:
        """Yield bytes as they arrive (chunked bodies are not held back until `size`)."""
        while not self._closed.is_set():
            try:
                data = self._response.read1(size)
            except (OSError, http.client.HTTPException, AttributeError, ValueError) as exc:
                # AttributeError/ValueError: the response was closed under a read in progress.
                if self._closed.is_set():
                    return
                raise ProviderError("unreachable", f"the audio stream broke: {exc}") from exc
            if not data:
                return
            yield data

    def close(self) -> None:
        """Drop the connection without blocking the caller (safe from another thread): the
        provider sees it as a cancel."""
        self._closed.set()
        sock = self._raw_sock or self._connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # Closing the response waits for a read in progress on another thread, and on Windows
        # the shutdown does not always wake that read: release the rest aside.
        threading.Thread(target=self._release, daemon=True, name="tts-stream-close").start()

    def _release(self) -> None:
        # The response's file object keeps the socket open after the connection lets go of it:
        # close it too, or the provider would only see the cancel, never the hang-up.
        for closer in (self._response.close, self._connection.close):
            try:
                closer()
            except (OSError, ValueError, AttributeError):
                pass


class ProviderClient(HttpProvider):
    """One TTS provider at one URL. Every call opens its own short connection, except the audio
    stream, which keeps its connection open until it ends or is closed."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        token: str | None = None,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_S,
        read_timeout: float = READ_TIMEOUT_S,
    ) -> None:
        super().__init__(url, token, connect_timeout=connect_timeout, read_timeout=read_timeout)

    # --- the endpoints -----------------------------------------------------------------------------------

    def info(self) -> ProviderInfo:
        """GET /v1/info, checked: the protocol name and version must be ours."""
        return parse_info(self._json("GET", "/v1/info"))

    def cancel(self, request_id: str) -> None:
        self._json("POST", "/v1/cancel", {"request_id": request_id}, timeout=CANCEL_TIMEOUT_S)

    def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
        audio_format: str = FORMAT_S16,
        options: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        default_rate: int = 24000,
    ) -> SpeechStream:
        """POST /v1/speak and return the stream once the response headers are in."""
        request_id = request_id or uuid.uuid4().hex
        body = {
            "request_id": request_id,
            "text": text,
            "voice": voice or None,
            "language": language,
            "format": audio_format,
            "options": dict(options or {}),
        }
        connection = self._connection(self._read_timeout)
        try:
            connection.request(
                "POST", self._path("/v1/speak"), json.dumps(body).encode("utf-8"), self._headers("application/json")
            )
            raw_sock = connection.sock  # getresponse() clears it for a body that ends with the connection
            response = connection.getresponse()
        except (OSError, http.client.HTTPException, ValueError) as exc:
            connection.close()
            raise ProviderError("unreachable", describe_os_error(exc, self.url)) from exc
        if response.status != 200:
            try:
                raise error_from(response.status, response.read())
            finally:
                connection.close()
        try:
            rate = int(response.getheader("X-VoiceMate-Sample-Rate") or default_rate)
            channels = int(response.getheader("X-VoiceMate-Channels") or 1)
        except ValueError:
            connection.close()
            raise ProviderError("protocol", "the X-VoiceMate-* audio headers are not numbers") from None
        reported = response.getheader("X-VoiceMate-Format") or audio_format
        if reported not in SPEAK_FORMATS or rate <= 0 or channels < 1:
            connection.close()
            raise ProviderError("protocol", f"unusable audio headers (format {reported}, {rate} Hz, {channels} ch)")
        return SpeechStream(connection, response, request_id, rate, channels, reported, raw_sock)


# --- parsing -------------------------------------------------------------------------------------------


def parse_info(data: Mapping[str, Any]) -> ProviderInfo:
    if data.get("protocol") != PROTOCOL_NAME:
        raise ProviderError("protocol", f"not a VoiceMate TTS provider (protocol {data.get('protocol')!r})")
    version = data.get("version")
    if version != PROTOCOL_VERSION or isinstance(version, bool):
        raise ProviderError("protocol", f"unsupported protocol version {version!r} (VoiceMate speaks 1)")
    rate = data.get("sample_rate")
    if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
        raise ProviderError("protocol", "/v1/info needs a positive integer sample_rate")
    formats = strings(data.get("formats"))
    if FORMAT_S16 not in formats:
        raise ProviderError("protocol", f"/v1/info formats must include {FORMAT_S16}")
    max_chars = data.get("max_chars")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        max_chars = None
    voices: list[Voice] = []
    raw_voices = data.get("voices")
    for raw in raw_voices if isinstance(raw_voices, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            continue
        name = raw.get("name")
        voices.append(
            Voice(
                id=raw["id"],
                name=name if isinstance(name, str) and name else raw["id"],
                languages=strings(raw.get("languages")),
                default=raw.get("default") is True,
            )
        )
    name = data.get("name")
    return ProviderInfo(
        name=name if isinstance(name, str) and name else "TTS provider",
        voices=tuple(voices),
        sample_rate=rate,
        formats=formats,
        max_chars=max_chars,
        features=strings(data.get("features")),
    )
