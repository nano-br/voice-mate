"""The engine's voice over HTTP: protocol `voicemate-tts` v1 (docs/tts-provider-protocol.md) under `/tts`.

The companion plays the assistant's replies with the voice the person configured in the engine,
so it asks the engine, never a provider: `<daemon>/tts/v1/info`, `/v1/health`, `/v1/speak`
(streamed PCM), `/v1/cancel` and `/v1/warmup`, served by `ApiServer` (same port, same token,
same browser hardening). The audio comes from the engine's speaker when it is a
`PcmSynthesizer` (Kokoro, and the external provider, which the engine simply relays). Other
engines answer `503 not_ready` with "engine not supported for the assistant voice".

One voice at a time (`SpeechGate`): while the engine's own claude_chat flow speaks, `/v1/speak`
answers `409 busy`; when the flow starts speaking during a stream, the stream ends early.
Error bodies follow the protocol: `{"error": {"code": ..., "message": ...}}`, English.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from app.features.tts.base import NullSpeaker, TextToSpeech
from app.features.tts.pcm import (
    GatedSpeaker,
    PcmStream,
    PcmSynthesizer,
    SpeechGate,
    SynthesisError,
    to_pcm_bytes,
)
from app.protocol.tts_provider import (
    FORMAT_F32,
    FORMAT_S16,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    SPEAK_FORMATS,
)

PREFIX: Final = "/tts"
DEFAULT_VOICE_ID: Final = "default"
MAX_CHARS: Final = 2000  # when the synthesizer names no limit of its own
UNSUPPORTED: Final = "engine not supported for the assistant voice"

_STATUS: Final[dict[str, int]] = {
    "bad_request": 400,
    "text_too_long": 400,
    "unknown_voice": 400,
    "unsupported_format": 400,
    "unauthorized": 401,
    "busy": 409,
    "not_ready": 503,
    "internal": 500,
}

EndpointState = Literal["loading", "ready", "unsupported", "off", "closed"]


@dataclass(frozen=True)
class TtsReply:
    """A JSON answer (`body`) or, for `/v1/speak`, a PCM stream with its audio headers."""

    status: int
    body: Mapping[str, object] | None = None
    stream: AudioBody | None = None
    headers: tuple[tuple[str, str], ...] = ()


class AudioBody:
    """The body of a `/v1/speak` answer. The server iterates it and ALWAYS calls `close` after,
    sent or not: that stops the synthesis and frees the speech gate."""

    def __init__(self, stream: PcmStream, audio_format: str, on_close: Callable[[], None]) -> None:
        self._stream = stream
        self._format = audio_format
        self._on_close = on_close
        self._closed = False
        self._lock = threading.Lock()

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._stream.chunks():
            data = to_pcm_bytes(chunk, self._format)
            if data:
                yield data

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._stream.close()
        finally:
            self._on_close()


def tts_error(code: str, message: str) -> TtsReply:
    return TtsReply(_STATUS.get(code, 500), {"error": {"code": code, "message": message}})


def is_tts_path(path: str) -> bool:
    return path == PREFIX or path.startswith(PREFIX + "/")


class TtsEndpoint:
    """The `/tts` routes. `attach` once the engine's speaker exists; until then it is loading."""

    def __init__(self, gate: SpeechGate | None = None) -> None:
        self.gate = gate or SpeechGate()
        self._lock = threading.Lock()
        self._state: EndpointState = "loading"
        self._reason = "the engine is loading"
        self._synth: PcmSynthesizer | None = None
        self._active: dict[str, PcmStream] = {}
        self._opening: set[str] = set()  # /v1/speak requests still waiting for their stream
        self._cancelled: set[str] = set()  # ... that were cancelled meanwhile

    @property
    def state(self) -> EndpointState:
        with self._lock:
            return self._state

    def attach(self, speaker: TextToSpeech | None) -> None:
        """The engine's speaker (possibly wrapped in a GatedSpeaker), or None without TTS."""
        inner = speaker.inner if isinstance(speaker, GatedSpeaker) else speaker
        with self._lock:
            if self._state == "closed":
                return
            if inner is None or isinstance(inner, NullSpeaker) or not inner.is_active():
                self._state, self._reason, self._synth = "off", "text to speech is off in the engine", None
            elif isinstance(inner, PcmSynthesizer):
                self._state, self._reason, self._synth = "ready", "", inner
            else:
                self._state, self._reason, self._synth = "unsupported", UNSUPPORTED, None

    def close(self) -> None:
        """Shutting down: stop every stream so the server can drain."""
        with self._lock:
            self._state = "closed"
            self._reason = "the engine is shutting down"
            streams = list(self._active.values())
            self._active.clear()
        for stream in streams:
            stream.close()

    # -- dispatch -------------------------------------------------------------------------------

    def handle(self, method: str, path: str, payload: Mapping[str, object]) -> TtsReply:
        """`path` is the full request path (with the prefix); `payload` the parsed JSON body."""
        route = path[len(PREFIX) :]
        if method == "GET" and route == "/v1/info":
            return self._info()
        if method == "GET" and route == "/v1/health":
            return self._health()
        if method == "POST" and route == "/v1/speak":
            return self._speak(payload)
        if method == "POST" and route == "/v1/cancel":
            return self._cancel(payload)
        if method == "POST" and route == "/v1/warmup":
            return self._warmup()
        return tts_error("bad_request", f"unknown endpoint: {method} {path}")

    def _ready(self) -> PcmSynthesizer | TtsReply:
        with self._lock:
            state, reason, synth = self._state, self._reason, self._synth
        if state == "ready" and synth is not None:
            return synth
        return tts_error("not_ready", reason)

    def _info(self) -> TtsReply:
        synth = self._ready()
        if isinstance(synth, TtsReply):
            return synth
        try:
            info = synth.pcm_info()
        except SynthesisError as exc:
            return tts_error(exc.code, exc.message)
        voices = [
            {"id": v.id, "name": v.name, "languages": list(v.languages), "default": v.default} for v in info.voices
        ] or [{"id": DEFAULT_VOICE_ID, "name": "Default", "languages": [], "default": True}]
        return TtsReply(
            200,
            {
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "name": info.name,
                "voices": voices,
                "sample_rate": info.sample_rate,
                "formats": [FORMAT_S16, FORMAT_F32],
                "max_chars": info.max_chars or MAX_CHARS,
                "features": list(info.features),
            },
        )

    @staticmethod
    def _voice_for(synth: PcmSynthesizer, voice: str | None) -> str | None:
        """None = the configured voice. The id `default` only means that when the synthesizer has
        no voices of its own (/v1/info then offers the synthetic `default`); a real voice with
        that id goes through."""
        if voice in (None, ""):
            return None
        if voice == DEFAULT_VOICE_ID and not synth.pcm_info().voices:
            return None
        return voice

    def _health(self) -> TtsReply:
        with self._lock:
            state = self._state
        if state == "loading":
            return TtsReply(200, {"status": "loading"})
        synth = self._ready()
        if isinstance(synth, TtsReply):
            return synth
        return TtsReply(200, {"status": "ok" if synth.pcm_ready() else "loading"})

    def _warmup(self) -> TtsReply:
        synth = self._ready()
        if isinstance(synth, TtsReply):
            return synth
        synth.pcm_warmup()
        return TtsReply(200, {})

    def _cancel(self, payload: Mapping[str, object]) -> TtsReply:
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            return tts_error("bad_request", "request_id is required")
        with self._lock:
            stream = self._active.pop(request_id, None)
            if stream is None and request_id in self._opening:
                self._cancelled.add(request_id)  # `_speak` drops the stream as soon as it exists
        if stream is not None:
            stream.close()
        return TtsReply(200, {})

    def _done_opening(self, request_id: str) -> bool:
        """The request has its stream (or failed). True when it was cancelled while opening."""
        with self._lock:
            self._opening.discard(request_id)
            if request_id in self._cancelled:
                self._cancelled.discard(request_id)
                return True
            return False

    def _speak(self, payload: Mapping[str, object]) -> TtsReply:
        synth = self._ready()
        if isinstance(synth, TtsReply):
            return synth
        request_id, text = payload.get("request_id"), payload.get("text")
        voice = payload.get("voice")
        audio_format = payload.get("format") or FORMAT_S16
        if not isinstance(request_id, str) or not request_id or not isinstance(text, str) or not text.strip():
            return tts_error("bad_request", "request_id and text are required")
        if voice is not None and not isinstance(voice, str):
            return tts_error("bad_request", "voice must be a string or null")
        if audio_format not in SPEAK_FORMATS:
            return tts_error("unsupported_format", f"unsupported format {audio_format!r}")
        if len(text) > MAX_CHARS:
            return tts_error("text_too_long", f"at most {MAX_CHARS} characters")
        if not self.gate.begin_remote():
            return tts_error("busy", "VoiceMate is already speaking")
        with self._lock:
            self._opening.add(request_id)
        try:
            stream = synth.open_pcm(text, voice=self._voice_for(synth, voice))
        except SynthesisError as exc:
            self._done_opening(request_id)
            self.gate.end_remote()
            return tts_error(exc.code, exc.message)
        except Exception as exc:  # noqa: BLE001 (request boundary: the gate must be released)
            self._done_opening(request_id)
            self.gate.end_remote()
            return tts_error("internal", f"synthesis failed: {exc}")
        if self._done_opening(request_id):
            stream.close()
            self.gate.end_remote()
            return tts_error("bad_request", "the request was cancelled")
        if not self.gate.hold_remote(stream):
            stream.close()
            self.gate.end_remote()  # no-op unless the gate is still ours: wakes the flow waiting for us
            return tts_error("busy", "VoiceMate is already speaking")
        with self._lock:
            self._active[request_id] = stream
        headers = (
            ("X-VoiceMate-Sample-Rate", str(stream.sample_rate)),
            ("X-VoiceMate-Channels", "1"),
            ("X-VoiceMate-Format", str(audio_format)),
        )

        def finished() -> None:
            with self._lock:
                if self._active.get(request_id) is stream:
                    del self._active[request_id]
            self.gate.end_remote()

        return TtsReply(200, stream=AudioBody(stream, str(audio_format), finished), headers=headers)
