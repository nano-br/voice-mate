"""Speech as PCM for someone else to play, and one voice at a time.

A speaker normally plays what it synthesizes (`TextToSpeech.speak`). The engine also serves
its voice to the companion (`/tts`, docs/tts-provider-protocol.md, served by
`app.daemon.tts_endpoint`), which plays the audio itself: for that a speaker may also be a
`PcmSynthesizer`, which hands the audio over as mono float32 chunks instead of playing it.
A speaker that is not one simply has no voice to serve.

`SpeechGate` makes sure the two never talk at once: the engine's own flow (the claude_chat
answers, played locally) holds it for a whole turn, and while it does the endpoint answers
"busy". When the flow starts speaking while the endpoint is streaming, the flow wins: the
stream is stopped first. `GatedSpeaker` wraps the engine's speaker to take and release it.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final, Literal, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray

from app.features.tts.base import TextToSpeech

# How long the engine's own flow waits for a stream it interrupted to let go of the model.
PREEMPT_WAIT_S: Final = 2.0

SynthesisErrorCode = Literal["not_ready", "unknown_voice", "busy", "bad_request", "text_too_long", "internal"]


class SynthesisError(Exception):
    """Why a synthesis cannot start. `code` is a protocol error code; `message` is English."""

    def __init__(self, code: SynthesisErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: SynthesisErrorCode = code
        self.message = message


@dataclass(frozen=True)
class PcmVoice:
    id: str
    name: str
    languages: tuple[str, ...] = ()
    default: bool = False


@dataclass(frozen=True)
class PcmInfo:
    """What a synthesizer offers. No voices means one unnamed default voice."""

    name: str
    sample_rate: int
    voices: tuple[PcmVoice, ...] = ()
    features: tuple[str, ...] = ()  # protocol features ("streaming", "expression_tags")
    max_chars: int | None = None


class PcmStream(Protocol):
    """One synthesis in progress. `chunks` ends early once `close` ran (from any thread)."""

    sample_rate: int

    def chunks(self) -> Iterator[NDArray[np.float32]]: ...

    def close(self) -> None: ...


@runtime_checkable
class PcmSynthesizer(Protocol):
    """A speaker that can also hand its audio over instead of playing it."""

    def pcm_info(self) -> PcmInfo:
        """Raises SynthesisError("not_ready") when the voice cannot be used (yet)."""
        ...

    def pcm_ready(self) -> bool:
        """True when a synthesis can start now, False while the model loads."""
        ...

    def pcm_warmup(self) -> None: ...

    def open_pcm(self, text: str, *, voice: str | None) -> PcmStream:
        """Start synthesizing `text` (None = the configured voice). Raises SynthesisError."""
        ...


Owner = Literal["local", "remote"]


class SpeechGate:
    """One voice at a time between the engine's own flow ("local") and the endpoint ("remote")."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._owner: Owner | None = None
        self._remote: PcmStream | None = None
        # The flow started speaking while a stream was still opening (no stream to close yet).
        self._preempted = False

    @property
    def owner(self) -> Owner | None:
        with self._cond:
            return self._owner

    def begin_local(self) -> None:
        """The flow speaks: interrupt the endpoint's stream (and wait briefly for it to end)."""
        with self._cond:
            if self._owner == "local":
                return
            remote = self._remote if self._owner == "remote" else None
            if self._owner == "remote" and remote is None:
                self._preempted = True  # `hold_remote` will refuse the stream still opening
        if remote is not None:
            remote.close()
        with self._cond:
            self._cond.wait_for(lambda: self._owner != "remote", timeout=PREEMPT_WAIT_S)
            self._owner = "local"
            self._remote = None

    def end_local(self) -> None:
        with self._cond:
            if self._owner == "local":
                self._owner = None
                self._cond.notify_all()

    def begin_remote(self) -> bool:
        """False while anything else speaks (the flow, or another stream)."""
        with self._cond:
            if self._owner is not None:
                return False
            self._owner = "remote"
            self._preempted = False
            return True

    def hold_remote(self, stream: PcmStream) -> bool:
        """The stream the flow interrupts when it starts speaking. False: the flow took over
        meanwhile, and the caller must close the stream and give up."""
        with self._cond:
            if self._owner == "remote" and not self._preempted:
                self._remote = stream
                return True
            return False

    def end_remote(self) -> None:
        with self._cond:
            if self._owner == "remote":
                self._owner = None
                self._remote = None
                self._preempted = False
                self._cond.notify_all()


class GatedSpeaker:
    """The engine's speaker, holding the gate from its first sentence to the end of the turn."""

    def __init__(self, inner: TextToSpeech, gate: SpeechGate) -> None:
        self.inner = inner
        self._gate = gate

    def is_active(self) -> bool:
        return self.inner.is_active()

    def speak(self, text: str) -> None:
        self._gate.begin_local()
        self.inner.speak(text)

    def warmup(self) -> None:
        self.inner.warmup()

    def wait_done(self, timeout: float | None = None) -> bool:
        try:
            return self.inner.wait_done(timeout)
        finally:
            self._gate.end_local()

    def stop(self) -> None:
        try:
            self.inner.stop()
        finally:
            self._gate.end_local()

    def end_turn(self) -> None:
        """The turn ended (also after an error): the endpoint may speak again."""
        self._gate.end_local()

    def close(self) -> None:
        try:
            self.inner.close()
        finally:
            self._gate.end_local()


def to_pcm_bytes(samples: NDArray[np.float32], audio_format: str) -> bytes:
    """Mono float32 samples -> little endian `pcm_s16le` (default) or `pcm_f32le` bytes."""
    if audio_format == "pcm_f32le":
        return np.asarray(samples, dtype="<f4").tobytes()
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()
