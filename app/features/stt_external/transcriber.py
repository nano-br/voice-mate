"""Transcriber for an external STT provider (docs/stt-provider-protocol.md, protocol v1).

The provider is any HTTP service that implements the protocol; VoiceMate knows nothing about
what is behind it. The transcriber checks `/v1/info` (protocol, version, languages, sample rates)
and `/v1/health`, then sends each recording to `/v1/transcribe` as mono 16-bit PCM.

A provider that cannot be used does not fail the recording while the fallback is on: that
recording is transcribed by the built-in engine (the normal `build_transcriber` chain), which is
built the first time it is needed and kept for later failures.

- Unreachable, `503 not_ready` and `5xx`: an outage. Only its first episode is logged; the
  provider is left alone for `RETRY_AFTER_SECONDS`, then tried again.
- `401`, a service that is not a `voicemate-stt` v1 provider, `unsupported_format` and
  `unsupported_language`: permanent. Reported once; the built-in engine is used until the
  settings change (the engine restarts with them).
- `409 busy` and the other `4xx` (`audio_too_long`, `bad_request`): only that recording.

With the fallback off, every failure surfaces as the recording's error, the way a failing
built-in backend does, and the provider is tried again on the next recording.

With `start_command` set, a provider that does not answer is started by the engine
(`ProviderLauncher`), as for the TTS provider. The token is never logged.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable
from typing import Final

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config
from app.core.transcription_backend import TranscriptionBackend
from app.features.tts.provider_launcher import ProviderLauncher
from app.i18n import _
from app.protocol.models import SttBackendName, SttDevice
from app.protocol.provider_http import ProviderError
from app.protocol.stt_provider import SttProviderClient, SttProviderInfo

# How long a provider that failed to answer is left alone before the next attempt.
RETRY_AFTER_SECONDS: Final = 10.0
# While the provider reports "loading", how long to wait for it (models can take minutes).
LOADING_TIMEOUT_SECONDS: Final = 180.0
LOADING_POLL_SECONDS: Final = 1.0
# The name `/health` reports until the provider has said its own.
UNKNOWN_NAME: Final = "external provider"

_OUTAGE: Final = ("unreachable", "not_ready", "server")
_PERMANENT_CODES: Final = ("unsupported_format", "unsupported_language")


def to_pcm16(audio: NDArray[np.float32], rate: int, target_rate: int) -> bytes:
    """Mono float32 [-1, 1] at `rate` -> little endian signed 16-bit PCM at `target_rate`."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if target_rate != rate and samples.size:
        count = max(1, round(samples.size * target_rate / rate))
        positions = np.arange(count, dtype=np.float64) * (rate / target_rate)
        samples = np.interp(positions, np.arange(samples.size, dtype=np.float64), samples).astype(np.float32)
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


class ExternalTranscriber:
    """Sends each recording to an external provider, with the built-in engine as the fallback."""

    name: SttBackendName = "external"
    # The provider's own device is unknown to VoiceMate; the engine side does no GPU work for it.
    device: SttDevice = "cpu"

    def __init__(
        self,
        config: Config,
        *,
        builtin: Callable[[], TranscriptionBackend],
        client: SttProviderClient | None = None,
        launcher: ProviderLauncher | None = None,
    ) -> None:
        settings = config.stt_external
        self._url = settings.url
        self._options = dict(settings.options)
        self._fallback_on = settings.fallback
        self._sample_rate = config.sample_rate
        language = config.transcription_language
        self._language: str | None = None if language == "auto" else language
        self._client = client or SttProviderClient(settings.url, settings.token or None)
        self._launcher = launcher or ProviderLauncher(settings.start_command, self._client.health, service="stt")
        self._launcher.start_soon()  # with the engine: the provider is loading by the first recording
        self._builtin_factory = builtin
        self._builtin: TranscriptionBackend | None = None
        self._lock = threading.RLock()  # one call at a time: warmup, transcriptions, close
        self._info: SttProviderInfo | None = None
        self._failure: str | None = None  # why the provider cannot work with VoiceMate (permanent)
        self._down_until = 0.0
        self._outage_logged = False
        self.model_name = self._probe_name()

    # --- TranscriptionBackend -----------------------------------------------------------------------------

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        with self._lock:
            if self._failure is not None:
                if not self._fallback_on:
                    raise RuntimeError(self._failure)
            elif not self._fallback_on or self._now() >= self._down_until:
                try:
                    return self._transcribe_with_provider(audio)
                except ProviderError as exc:
                    message = self._report(exc)
                    if not self._fallback_on:
                        raise RuntimeError(message) from exc
            return self._fallback().transcribe(audio)

    def warmup(self) -> None:
        """Check the provider and let it load what the first recording needs."""
        with self._lock:
            if self._failure is not None:
                return
            try:
                self._ensure_ready()
            except ProviderError as exc:
                self._report(exc)
                return
            try:
                self._client.warmup()
            except ProviderError as exc:  # warmup is optional in the protocol and only an optimization
                print(_("[ExternalTranscriber] warmup skipped: {detail}").format(detail=exc.detail), file=sys.stderr)

    def close(self) -> None:
        with self._lock:
            builtin, self._builtin = self._builtin, None
        close = getattr(builtin, "close", None)
        if callable(close):
            close()

    # --- provider ----------------------------------------------------------------------------------------

    def _now(self) -> float:
        return time.monotonic()

    def _probe_name(self) -> str:
        """The provider's name for `/health`, asked once at startup (silently: warmup reports)."""
        try:
            return self._client.info().name
        except ProviderError:
            return UNKNOWN_NAME

    def _transcribe_with_provider(self, audio: NDArray[np.float32]) -> str:
        info = self._ensure_ready()
        seconds = audio.size / self._sample_rate
        if info.max_seconds is not None and seconds > info.max_seconds:
            raise ProviderError(
                "rejected",
                f"the recording lasts {seconds:.1f} s and the provider takes at most {info.max_seconds:g} s",
                code="audio_too_long",
            )
        if not info.accepts_language(self._language):
            raise ProviderError(
                "rejected",
                f"the provider does not list the language '{self._language}'",
                code="unsupported_language",
            )
        rate = info.sample_rate_for(self._sample_rate)
        result = self._client.transcribe(
            to_pcm16(audio, self._sample_rate, rate),
            sample_rate=rate,
            language=self._language,
            options=self._options,
        )
        return result.text

    def _ensure_ready(self) -> SttProviderInfo:
        """The provider's info once it answered and said it is ready (raises ProviderError otherwise)."""
        if self._info is not None:
            return self._info
        try:
            info = self._client.info()
            self._wait_until_healthy()
        except ProviderError as exc:
            # `/v1/info` or `/v1/health` answered 4xx: the URL points at some other web server.
            if exc.kind == "rejected":
                raise ProviderError("protocol", f"the provider endpoints were refused ({exc.detail})") from exc
            raise
        self._info = info
        self.model_name = info.name
        self._outage_logged = False
        languages = ", ".join(info.languages) or _("any language")
        print(
            _("[ExternalTranscriber] Connected to '{name}' ({languages}).").format(name=info.name, languages=languages)
        )
        return info

    def _wait_until_healthy(self) -> None:
        deadline = self._now() + LOADING_TIMEOUT_SECONDS
        announced = False
        while self._client.health() == "loading":
            if self._now() > deadline:
                raise ProviderError("not_ready", "the provider is still loading its models")
            if not announced:
                print(_("[ExternalTranscriber] The provider is loading, waiting for it..."))
                announced = True
            time.sleep(LOADING_POLL_SECONDS)

    def _fallback(self) -> TranscriptionBackend:
        """The built-in engine, built the first time the provider cannot transcribe."""
        if self._builtin is None:
            print(_("[ExternalTranscriber] Loading the built-in transcription engine..."))
            builtin = self._builtin_factory()
            warmup = getattr(builtin, "warmup", None)
            if callable(warmup):
                warmup()
            self._builtin = builtin
        return self._builtin

    def _report(self, exc: ProviderError) -> str:
        """Say what is wrong (an outage only once) and take the provider out of play: for good
        when VoiceMate and the provider cannot work together, for a few seconds when it did not
        answer. Returns the explanation, for the recording's error when the fallback is off."""
        permanent = exc.kind in ("protocol", "unauthorized") or exc.code in _PERMANENT_CODES
        outage = not permanent and exc.kind in _OUTAGE
        if exc.kind == "unreachable":
            self._launcher.start_soon()
        message = _explain(exc, self._url)
        if permanent:
            self._failure = message
        elif outage:
            self._down_until = self._now() + RETRY_AFTER_SECONDS
            self._info = None  # check the provider again when it comes back
        if outage and self._outage_logged:
            return message
        if outage:
            self._outage_logged = True
        print(_("[ExternalTranscriber] ⚠ {message}").format(message=message), file=sys.stderr)
        if self._fallback_on:
            if permanent:
                note = _("[ExternalTranscriber]   Using the built-in transcription engine until the settings change.")
            elif outage:
                note = _(
                    "[ExternalTranscriber]   Using the built-in transcription engine meanwhile; "
                    "the provider is tried again in a few seconds."
                )
            else:
                note = _("[ExternalTranscriber]   Using the built-in transcription engine for this recording.")
            print(note, file=sys.stderr)
        return message


def _explain(exc: ProviderError, url: str) -> str:
    if exc.kind == "unreachable":
        return _("The STT provider at {url} is not reachable ({detail}).").format(url=url, detail=exc.detail)
    if exc.kind == "unauthorized":
        return _("The STT provider rejected the token. Set it in the saved config or in VOICEMATE_STT_TOKEN.")
    if exc.kind == "protocol":
        return _("The service at {url} is not a compatible STT provider: {detail}").format(url=url, detail=exc.detail)
    if exc.kind == "not_ready":
        return _("The STT provider at {url} is not ready yet ({detail}).").format(url=url, detail=exc.detail)
    if exc.kind == "busy":
        return _("The STT provider at {url} is busy ({detail}).").format(url=url, detail=exc.detail)
    if exc.kind == "rejected":
        return _("The STT provider at {url} refused the recording: {detail}").format(url=url, detail=exc.detail)
    return _("The STT provider at {url} failed: {detail}").format(url=url, detail=exc.detail)
