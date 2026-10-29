"""Speaker for an external TTS provider (docs/tts-provider-protocol.md, protocol v1).

The provider is any HTTP service that implements the protocol; VoiceMate knows nothing about
what is behind it. The speaker checks `/v1/info` (protocol, version, voices, features) and
`/v1/health`, then streams the PCM of each `/v1/speak` response straight into the `AudioSink`
(the same persistent player the local engines use), so the first words play while the rest of
the sentence is still being synthesized.

Requests are serialized (the protocol allows one at a time). `stop()` plays silence at once,
asks the provider to stop with `/v1/cancel` and closes the connection. A provider that is down
is not an error that crashes the turn: the speaker reports itself inactive for a few seconds
(the handler falls back to the beep) and tries again afterwards.

As a `PcmSynthesizer` it is also the engine's `/tts` voice for the companion: `open_pcm` relays
the provider's stream as chunks instead of playing it, so the companion never needs to reach
the provider itself (inside or outside WSL).
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Iterator

import numpy as np
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.features.tts.audio_player import AudioSink, create_audio_player
from app.features.tts.pcm import PcmInfo, PcmVoice, SynthesisError
from app.i18n import _
from app.protocol.tts_provider import (
    FEATURE_EXPRESSION_TAGS,
    FEATURE_STREAMING,
    FORMAT_F32,
    ProviderClient,
    ProviderError,
    ProviderInfo,
    SpeechStream,
    language_for_provider,
    split_text,
    strip_expression_tags,
)

# How long a provider that failed to answer is left alone before the next attempt.
RETRY_AFTER_SECONDS = 10.0
# While the provider reports "loading", how long to wait for it (models can take minutes).
LOADING_TIMEOUT_SECONDS = 180.0
LOADING_POLL_SECONDS = 1.0


class ExternalSpeaker:
    """Streams speech from an external provider into the audio player."""

    def __init__(
        self,
        config: TTSConfig,
        *,
        client: ProviderClient | None = None,
        player: AudioSink | None = None,
    ) -> None:
        self._config = config
        self._client = client or ProviderClient(config.external_url, config.external_token or None)
        self._player: AudioSink = player or create_audio_player()
        self._stop_event = threading.Event()
        self._speak_lock = threading.Lock()  # one request at a time, in order
        self._state_lock = threading.Lock()  # guards the fields below (stop() runs on another thread)
        self._stream: SpeechStream | None = None
        self._info: ProviderInfo | None = None
        self._closed = False
        self._failed = False  # the provider cannot work with VoiceMate (wrong protocol, bad token)
        self._down_until = 0.0
        self._outage_logged = False

    # --- TextToSpeech ------------------------------------------------------------------------------------

    def is_active(self) -> bool:
        with self._state_lock:
            return not self._closed and not self._failed and self._now() >= self._down_until

    def speak(self, text: str) -> None:
        """Send the text to the provider and enqueue its audio as it arrives."""
        if self._closed or not text.strip():
            return
        with self._speak_lock:
            self._stop_event.clear()
            if not self.is_active():
                return
            info = self._ensure_ready()
            if info is None:
                return
            if not info.supports(FEATURE_EXPRESSION_TAGS):
                text = strip_expression_tags(text)
            for piece in split_text(text, info.max_chars) if info.max_chars else [text.strip()]:
                if not piece or self._stop_event.is_set():
                    return
                if not self._speak_piece(piece, info):
                    return

    def warmup(self) -> None:
        """Check the provider and let it load what the first sentence needs."""
        if self._closed or self._failed:
            return
        with self._speak_lock:
            if self._ensure_ready() is None:
                return
            try:
                self._client.warmup()
            except ProviderError as exc:  # warmup is optional in the protocol and only an optimization
                print(_("[ExternalSpeaker] warmup skipped: {detail}").format(detail=exc.detail), file=sys.stderr)

    def wait_done(self, timeout: float | None = None) -> bool:
        """Wait for the queued audio to finish playing (end of turn)."""
        limit = timeout if timeout is not None else self._config.drain_timeout_seconds
        ok = self._player.drain(timeout=limit)
        if not ok:
            print(
                _("[ExternalSpeaker] ⚠ AudioPlayer drain timed out, audio may have been left incomplete."),
                file=sys.stderr,
            )
            self._player.abort()
        return ok

    def stop(self) -> None:
        """Silence now, then tell the provider to stop synthesizing and drop the connection."""
        self._stop_event.set()
        self._player.abort()
        with self._state_lock:
            stream = self._stream
        if stream is None:
            return
        try:
            self._client.cancel(stream.request_id)
        except ProviderError:
            pass  # closing the connection below stops it too, as the protocol requires
        stream.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()
        self._player.close()

    # --- PCM for the engine's /tts endpoint (PcmSynthesizer) ----------------------------------------------

    def pcm_info(self) -> PcmInfo:
        info = self._provider_info()
        return PcmInfo(
            info.name,
            info.sample_rate,
            tuple(PcmVoice(v.id, v.name, v.languages, v.default) for v in info.voices),
            tuple(f for f in info.features if f in (FEATURE_STREAMING, FEATURE_EXPRESSION_TAGS)),
            info.max_chars,
        )

    def pcm_ready(self) -> bool:
        try:
            return self._client.health() == "ok"
        except ProviderError:
            return False

    def pcm_warmup(self) -> None:
        try:
            self._client.warmup()
        except ProviderError as exc:  # optional in the protocol
            print(_("[ExternalSpeaker] warmup skipped: {detail}").format(detail=exc.detail), file=sys.stderr)

    def open_pcm(self, text: str, *, voice: str | None) -> _ProviderPcm:
        """Start the provider's stream (the caller holds the speech gate: one request at a time)."""
        info = self._provider_info()
        if not info.supports(FEATURE_EXPRESSION_TAGS):
            text = strip_expression_tags(text)
        if info.max_chars and len(text) > info.max_chars:
            raise SynthesisError("text_too_long", f"the text is longer than {info.max_chars} characters")
        config = self._config
        try:
            stream = self._client.speak(
                text,
                voice=voice or config.external_voice or None,
                language=language_for_provider(config.language),
                audio_format=info.speak_format,
                options=config.external_options,
                default_rate=info.sample_rate,
            )
        except ProviderError as exc:
            raise _synthesis_error(exc, config.external_url) from exc
        return _ProviderPcm(self._client, stream)

    def _provider_info(self) -> ProviderInfo:
        if self._closed or self._failed:
            raise SynthesisError("not_ready", "the TTS provider cannot be used with VoiceMate (see the engine log)")
        try:
            return self._client.info()
        except ProviderError as exc:
            raise _synthesis_error(exc, self._config.external_url) from exc

    # --- provider ----------------------------------------------------------------------------------------

    def _now(self) -> float:
        return time.monotonic()

    def _ensure_ready(self) -> ProviderInfo | None:
        """The provider's info once it answered and said it is ready; None (reported) otherwise."""
        if self._info is not None:
            return self._info
        try:
            info = self._client.info()
            self._wait_until_healthy()
        except ProviderError as exc:
            self._report(_as_protocol_error(exc) if exc.kind == "rejected" else exc)
            return None
        if self._stop_event.is_set():
            return None
        self._info = info
        self._outage_logged = False
        print(
            _("[ExternalSpeaker] Connected to '{name}' ({count} voices, {rate} Hz).").format(
                name=info.name, count=len(info.voices), rate=info.sample_rate
            )
        )
        return info

    def _wait_until_healthy(self) -> None:
        deadline = self._now() + LOADING_TIMEOUT_SECONDS
        announced = False
        while self._client.health() == "loading":
            if self._stop_event.is_set():
                return
            if self._now() > deadline:
                raise ProviderError("not_ready", "the provider is still loading its models")
            if not announced:
                print(_("[ExternalSpeaker] The provider is loading, waiting for it..."))
                announced = True
            time.sleep(LOADING_POLL_SECONDS)

    def _speak_piece(self, text: str, info: ProviderInfo) -> bool:
        """One /v1/speak request, played as it streams. False when the turn should stop speaking."""
        config = self._config
        try:
            stream = self._client.speak(
                text,
                voice=config.external_voice or None,
                language=language_for_provider(config.language),
                audio_format=info.speak_format,
                options=config.external_options,
                default_rate=info.sample_rate,
            )
        except ProviderError as exc:
            if self._stop_event.is_set():
                return False
            self._report(exc)
            return False
        with self._state_lock:
            self._stream = stream
        try:
            if self._stop_event.is_set():  # stop() ran between the response and now
                return False
            self._player.ensure_started(stream.sample_rate)
            for samples in _decode(stream):
                if self._stop_event.is_set():
                    return False
                if samples.size > 0:
                    self._player.feed(samples)
            return not self._stop_event.is_set()
        except ProviderError as exc:
            if not self._stop_event.is_set():
                self._report(exc)
            return False
        except Exception as exc:  # noqa: BLE001  (the audio device or numpy must not crash the turn)
            print(_("[ExternalSpeaker] error while speaking: {exc}").format(exc=exc), file=sys.stderr)
            self._player.abort()
            return False
        finally:
            with self._state_lock:
                self._stream = None
            stream.close()

    def _report(self, exc: ProviderError) -> None:
        """Say once what is wrong and take the speaker out of play: for good when VoiceMate and
        the provider cannot work together, for a few seconds when it just did not answer."""
        permanent = exc.kind in ("protocol", "unauthorized") or exc.code in ("unknown_voice", "unsupported_format")
        with self._state_lock:
            if permanent:
                self._failed = True
            elif exc.kind in ("unreachable", "not_ready", "server"):
                self._down_until = self._now() + RETRY_AFTER_SECONDS
                self._info = None  # check the provider again when it comes back
            outage = permanent or exc.kind in ("unreachable", "not_ready", "server")
            # A busy (409) or other 4xx answer does not take the speaker out of play: it is never
            # an outage, so it is not announced again and again either.
            first = outage and (permanent or not self._outage_logged)
            if outage:
                self._outage_logged = True
        if first:
            print(
                _("[ExternalSpeaker] ⚠ {message}").format(message=_explain(exc, self._config.external_url)),
                file=sys.stderr,
            )


class _ProviderPcm:
    """The provider's stream as float32 chunks; `close` cancels it (any thread, idempotent)."""

    def __init__(self, client: ProviderClient, stream: SpeechStream) -> None:
        self._client = client
        self._stream = stream
        self.sample_rate = stream.sample_rate
        self._closed = threading.Event()
        self._finished = False  # the provider ended the body: nothing to cancel

    def chunks(self) -> Iterator[NDArray[np.float32]]:
        try:
            for samples in _decode(self._stream):
                if self._closed.is_set():
                    return
                if samples.size > 0:
                    yield samples
            self._finished = True
        except ProviderError as exc:
            if not self._closed.is_set():
                print(_("[ExternalSpeaker] error while speaking: {exc}").format(exc=exc.detail), file=sys.stderr)
                raise  # not the end of the speech: the server drops the connection, cut short
        finally:
            self._stream.close()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if self._finished:
            self._stream.close()
            return
        try:
            self._client.cancel(self._stream.request_id)
        except ProviderError:
            pass  # closing the connection stops it too
        self._stream.close()


def _synthesis_error(exc: ProviderError, url: str) -> SynthesisError:
    """A provider problem as the engine's endpoint reports it (English, for the companion's log)."""
    if exc.kind == "busy":
        return SynthesisError("busy", "the TTS provider is busy")
    if exc.code == "text_too_long":
        return SynthesisError("text_too_long", exc.detail)
    if exc.code == "unknown_voice":
        return SynthesisError("unknown_voice", exc.detail)
    if exc.kind == "rejected":
        return SynthesisError("bad_request", exc.detail)
    return SynthesisError("not_ready", f"the TTS provider at {url} is not available ({exc.detail})")


def _as_protocol_error(exc: ProviderError) -> ProviderError:
    """`/v1/info` answered 4xx: the URL points at some other web server, not at a provider."""
    return ProviderError("protocol", f"/v1/info was refused ({exc.detail})", code=exc.code)


def _explain(exc: ProviderError, url: str) -> str:
    if exc.kind == "unreachable":
        return _("The TTS provider at {url} is not reachable ({detail}). Speech is off until it is back.").format(
            url=url, detail=exc.detail
        )
    if exc.kind == "unauthorized":
        return _("The TTS provider rejected the token. Set it in the settings or in VOICEMATE_TTS_TOKEN.")
    if exc.kind == "protocol":
        return _("The service at {url} is not a compatible TTS provider: {detail}").format(url=url, detail=exc.detail)
    if exc.kind == "not_ready":
        return _("The TTS provider at {url} is not ready yet ({detail}).").format(url=url, detail=exc.detail)
    return _("The TTS provider at {url} failed: {detail}").format(url=url, detail=exc.detail)


def _decode(stream: SpeechStream) -> Iterator[NDArray[np.float32]]:
    """PCM bytes -> mono float32 chunks, keeping a partial sample for the next read."""
    width = 4 if stream.audio_format == FORMAT_F32 else 2
    frame = width * stream.channels
    pending = b""
    for data in stream.chunks():
        pending += data
        usable = len(pending) - len(pending) % frame
        if usable == 0:
            continue
        block, pending = pending[:usable], pending[usable:]
        if width == 2:
            samples = np.frombuffer(block, dtype="<i2").astype(np.float32) / 32768.0
        else:
            samples = np.frombuffer(block, dtype="<f4").astype(np.float32)
        if stream.channels > 1:
            samples = samples.reshape(-1, stream.channels).mean(axis=1, dtype=np.float32)
        yield samples
