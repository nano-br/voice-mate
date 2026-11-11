"""Client for the external STT provider protocol, version 1 (docs/stt-provider-protocol.md).

Standard library only, like the TTS client (`app.protocol.tts_provider`), with which it shares the
URL, the errors and the plain HTTP calls (`app.protocol.provider_http`). The engine's transcriber
and `tools/stt_provider_check.py` talk to a provider through this one module. It deals in bytes:
turning the recording into 16-bit PCM is the caller's job.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.protocol.provider_http import CONNECT_TIMEOUT_S, HttpProvider, error_from, json_object, strings
from app.protocol.provider_http import ProviderError as ProviderError
from app.protocol.provider_http import url_is_valid as url_is_valid

PROTOCOL_NAME: Final = "voicemate-stt"
PROTOCOL_VERSION: Final = 1
DEFAULT_URL: Final = "http://127.0.0.1:8766"
TOKEN_ENV: Final = "VOICEMATE_STT_TOKEN"
# The command that starts the provider when it does not answer (`app.features.tts.provider_launcher`).
START_COMMAND_ENV: Final = "VOICEMATE_STT_START_COMMAND"

FORMAT_S16: Final = "pcm_s16le"
# A recording can be long (up to --max-recording-seconds): give the provider time to transcribe it.
TRANSCRIBE_TIMEOUT_S: Final = 120.0


@dataclass(frozen=True)
class SttProviderInfo:
    name: str
    languages: tuple[str, ...]  # empty = any language
    sample_rates: tuple[int, ...]
    formats: tuple[str, ...]
    max_seconds: float | None
    features: tuple[str, ...]

    def supports(self, feature: str) -> bool:
        return feature in self.features

    def accepts_language(self, language: str | None) -> bool:
        """True when the provider takes this ISO 639-1 code (None = let the provider detect it)."""
        return language is None or not self.languages or language in self.languages

    def sample_rate_for(self, recorded: int) -> int:
        """The rate VoiceMate sends: the recording's own when the provider takes it, else its first."""
        return recorded if recorded in self.sample_rates else self.sample_rates[0]


@dataclass(frozen=True)
class Transcription:
    text: str  # "" = nothing was heard
    language: str | None = None  # what the provider says it heard, when it says


def options_header(options: Mapping[str, Any]) -> str:
    """`X-VoiceMate-Options`: the options as compact ASCII JSON (one header line, any language)."""
    return json.dumps(dict(options), ensure_ascii=True, separators=(",", ":"))


class SttProviderClient(HttpProvider):
    """One STT provider at one URL. Every call opens its own short connection."""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        token: str | None = None,
        *,
        connect_timeout: float = CONNECT_TIMEOUT_S,
        read_timeout: float = TRANSCRIBE_TIMEOUT_S,
    ) -> None:
        super().__init__(url, token, connect_timeout=connect_timeout, read_timeout=read_timeout)

    def info(self) -> SttProviderInfo:
        """GET /v1/info, checked: the protocol name and version must be ours."""
        return parse_info(self._json("GET", "/v1/info"))

    def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        language: str | None = None,
        options: Mapping[str, Any] | None = None,
        request_id: str | None = None,
    ) -> Transcription:
        """POST /v1/transcribe with raw mono `pcm_s16le` audio and return what the provider heard."""
        headers = self._headers("application/octet-stream")
        headers["X-VoiceMate-Sample-Rate"] = str(sample_rate)
        headers["X-VoiceMate-Channels"] = "1"
        headers["X-VoiceMate-Format"] = FORMAT_S16
        headers["X-VoiceMate-Request-Id"] = request_id or uuid.uuid4().hex
        if language:
            headers["X-VoiceMate-Language"] = language
        if options:
            headers["X-VoiceMate-Options"] = options_header(options)
        status, raw = self._request("POST", "/v1/transcribe", pcm, headers, self._read_timeout)
        if status != 200:
            raise error_from(status, raw)
        return parse_transcription(json_object(raw, "/v1/transcribe"))


# --- parsing -------------------------------------------------------------------------------------------


def parse_info(data: Mapping[str, Any]) -> SttProviderInfo:
    if data.get("protocol") != PROTOCOL_NAME:
        raise ProviderError("protocol", f"not a VoiceMate STT provider (protocol {data.get('protocol')!r})")
    version = data.get("version")
    if version != PROTOCOL_VERSION or isinstance(version, bool):
        raise ProviderError("protocol", f"unsupported protocol version {version!r} (VoiceMate speaks 1)")
    raw_rates = data.get("sample_rates")
    rates = tuple(
        rate
        for rate in (raw_rates if isinstance(raw_rates, list) else [])
        if isinstance(rate, int) and not isinstance(rate, bool) and rate > 0
    )
    if not rates:
        raise ProviderError("protocol", "/v1/info needs sample_rates, a list of positive integers")
    formats = strings(data.get("formats"))
    if FORMAT_S16 not in formats:
        raise ProviderError("protocol", f"/v1/info formats must include {FORMAT_S16}")
    max_seconds = data.get("max_seconds")
    limit = (
        float(max_seconds)
        if isinstance(max_seconds, int | float) and not isinstance(max_seconds, bool) and max_seconds > 0
        else None
    )
    name = data.get("name")
    return SttProviderInfo(
        name=name if isinstance(name, str) and name else "STT provider",
        languages=strings(data.get("languages")),
        sample_rates=rates,
        formats=formats,
        max_seconds=limit,
        features=strings(data.get("features")),
    )


def parse_transcription(data: Mapping[str, Any]) -> Transcription:
    text = data.get("text")
    if not isinstance(text, str):
        raise ProviderError("protocol", '/v1/transcribe must answer {"text": "..."}')
    language = data.get("language")
    return Transcription(text.strip(), language if isinstance(language, str) and language else None)
