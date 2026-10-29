"""The assistant's voice: the engine speaks, this computer plays.

Every spoken line of the assistant (its replies' <voice> block and VoiceMate's own short notices)
goes the same way: the text, sentence by sentence, to the engine's `/tts` endpoint (protocol
`voicemate-tts` v1, docs/tts-provider-protocol.md), and the PCM it streams back to a `PcmOutput`
of this computer (`app.companion.win.pcm_out` on Windows, `app.companion.linux.pcm_out` on
Linux), in order. The next sentence is requested while the previous one still plays, so the only
gaps are the synthesis itself. The engine uses whatever voice the person configured there, and it
is the one that reaches an external provider, inside or outside WSL.

`stop()` silences at once: the queue is dropped, the audio flushed, the stream's connection
closed and the engine asked to cancel. While `hold()` is true (VoiceMate is recording the
microphone) nothing is said: the line being heard is cut and said again from its start once the
recording ends, and everything queued meanwhile waits for it. `pause()` holds right away, before
the recording shows in `hold()`. When the engine cannot speak (not running, no voice
engine, a voice engine it cannot serve), replies stay text only and a listener hears the reason
once per outage; the short notices then fall back to `fallback` (Windows speech).

One worker thread; every public method returns quickly and may be called from any thread.
Standard library only (no Qt, no numpy).
"""

from __future__ import annotations

import logging
import queue
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Final, Literal, Protocol

from app.i18n import _
from app.protocol.assistant_reply import strip_expressions
from app.protocol.tts_provider import (
    FEATURE_EXPRESSION_TAGS,
    FORMAT_S16,
    ProviderClient,
    ProviderError,
    ProviderInfo,
    SpeechStream,
    split_text,
)

log = logging.getLogger(__name__)

TTS_PREFIX: Final = "/tts"
# After the engine could not speak, the next attempt waits this long (notices go to the fallback).
RETRY_AFTER_S: Final = 30.0
# While the engine reports it is still loading its voice, the next attempt waits this long.
LOADING_RETRY_S: Final = 5.0
# A `busy` answer right after a stop is the engine still letting go of the previous stream:
# ask again for about a second (BUSY_TRIES x BUSY_STEP_S) before giving the line up.
BUSY_TRIES: Final = 10
BUSY_STEP_S: Final = 0.1
DEFAULT_MAX_CHARS: Final = 2000
IDLE_POLL_S: Final = 0.02
# How often a held voice looks whether the recording ended.
HOLD_POLL_S: Final = 0.05
# `pause()` holds this long by itself: the recording only shows in `hold()` once the engine
# confirms it started, and the voice must already be quiet when the microphone opens.
PAUSE_GRACE_S: Final = 1.0
# What `test()` says (the Assistant tab's Test button).
TEST_KEY: Final = "voice-test"

# A sentence ends at . ! ? or an ellipsis (also the CJK marks), maybe followed by quotes or
# brackets, then whitespace. Text without such an end waits for more (or for the end of the reply).
_SENTENCE_END: Final = re.compile(r"[.!?…。！？]+[\"'”’)\]]*(?=\s)")

Kind = Literal["reply", "notice"]
# How one /v1/speak request ended: played, stopped (the line is over) or cut by a hold.
Outcome = Literal["done", "stop", "held"]


class PcmOutput(Protocol):
    """A playback device for signed 16-bit PCM. Methods may be called from two threads (the
    worker writes, `stop` flushes): implementations lock themselves."""

    def open(self, sample_rate: int, channels: int) -> None:
        """Raises OSError when there is no usable output."""

    def write(self, data: bytes) -> None:
        """Queue whole frames (returns at once; the audio plays in order)."""

    def commit(self) -> None:
        """The end of a stream: play whatever is still held back for a smoother start."""

    def pending(self) -> bool:
        """True while queued audio has not finished playing."""

    def flush(self) -> None:
        """Drop everything queued and go silent now."""

    def close(self) -> None: ...


ClientFactory = Callable[[], ProviderClient | None]
OutputFactory = Callable[[], PcmOutput | None]
UnavailableListener = Callable[[str], None]


def engine_tts_url(host: str, port: int) -> str:
    return f"http://{host}:{port}{TTS_PREFIX}"


def default_output() -> PcmOutput | None:
    """This computer's PCM player (None where there is none)."""
    if sys.platform == "win32":
        from app.companion.win.pcm_out import WaveOutput

        try:
            return WaveOutput()
        except OSError:
            return None
    if sys.platform.startswith("linux"):
        from app.companion.linux.pcm_out import PipeOutput

        return PipeOutput()
    return None


def complete_sentences(text: str, final: bool) -> tuple[list[str], int]:
    """The whole sentences at the start of `text` and how many characters they used. With
    `final` the rest counts as a sentence too."""
    sentences: list[str] = []
    used = 0
    for match in _SENTENCE_END.finditer(text):
        sentence = text[used : match.end()].strip()
        if sentence:
            sentences.append(sentence)
        used = match.end()
    if final:
        rest = text[used:].strip()
        if rest:
            sentences.append(rest)
        used = len(text)
    return sentences, used


def explain(error: ProviderError) -> str:
    """Why the engine cannot speak, localized (the end of "No voice available: ...")."""
    detail = error.detail
    if error.kind == "unreachable":
        return _("the engine is not running")
    if error.kind == "unauthorized":
        return _("the engine refused the access token")
    if error.kind == "protocol":
        return _("the engine is too old for the assistant voice")
    if error.kind == "not_ready" and "not supported" in detail:
        return _("the voice engine set up in the engine cannot speak for the assistant")
    if error.kind == "not_ready" and "off" in detail:
        return _("text to speech is off in the engine")
    if error.kind == "not_ready" and "loading" in detail:
        return _("the engine is still loading")
    if error.kind == "not_ready":
        return _("the engine's voice is not available ({detail})").format(detail=detail)
    return _("the engine's voice failed ({detail})").format(detail=detail)


@dataclass(frozen=True)
class _Item:
    text: str
    kind: Kind
    language: str
    generation: int
    voice: str | None = None  # overrides the configured voice (the Test button)


class EngineVoice:
    """Speaks through the engine; see the module docstring."""

    def __init__(
        self,
        client: ClientFactory,
        output: OutputFactory,
        *,
        voice: Callable[[], str] = lambda: "",
        fallback: Callable[[str, str], None] = lambda _text, _language: None,
        hold: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_factory = client
        self._output_factory = output
        self._voice = voice
        self._fallback = fallback
        self._hold = hold
        self._clock = clock
        self._lock = threading.Lock()
        self._queue: queue.Queue[_Item | None] = queue.Queue()
        self._generation = 0
        self._replies: dict[str, int] = {}  # reply key -> characters already queued
        self._listeners: list[UnavailableListener] = []
        self._stream: SpeechStream | None = None
        self._stream_client: ProviderClient | None = None
        self._opening: tuple[ProviderClient, str] | None = None  # a /v1/speak still waiting for its headers
        self._output: PcmOutput | None = None
        self._output_format: tuple[int, int] | None = None
        self._info: ProviderInfo | None = None
        self._info_url = ""
        self._down_until = 0.0
        self._warming = False  # a /v1/warmup is running (the engine loads its voice on demand)
        self._reason = ""  # why the engine cannot speak ("" = it can, or it was not tried yet)
        self._closed = False
        self._thread: threading.Thread | None = None  # started with the first line
        self._paused_until = 0.0  # `pause()`: held until then even if `hold()` is not yet true
        self._pause_cut_audio = False  # `pause()` flushed audio that was still playing
        self._last_piece: _Item | None = None  # the piece most recently played to its end
        self._replay: _Item | None = None  # that piece, when a hold cut it while it still played

    def _put(self, item: _Item) -> None:
        with self._lock:
            if self._closed:
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="assistant-voice", daemon=True)
                self._thread.start()
        self._queue.put(item)

    # --- public ---------------------------------------------------------------------------------

    def on_unavailable(self, listener: UnavailableListener) -> None:
        """`listener(reason)` once each time the engine stops being able to speak (worker thread)."""
        with self._lock:
            self._listeners.append(listener)

    def speak(self, key: str, text: str, *, final: bool, language: str = "") -> None:
        """A reply's voice text so far (expression tags kept): its new whole sentences are
        queued; with `final` the rest too, and the reply is done."""
        with self._lock:
            if self._closed:
                return
            used = self._replies.get(key, 0)
            sentences, consumed = complete_sentences(text[used:], final)
            if final:
                self._replies.pop(key, None)
            else:
                self._replies[key] = used + consumed
            generation = self._generation
        for sentence in sentences:
            self._put(_Item(sentence, "reply", language, generation))

    def notice(self, text: str, language: str) -> None:
        """A short fixed line (an answer is ready, nothing new yet)."""
        text = text.strip()
        if not text:
            return
        with self._lock:
            if self._closed:
                return
            generation = self._generation
        self._put(_Item(text, "notice", language, generation))

    def test(self, text: str, voice: str, language: str = "") -> None:
        """Stop what is being said and say `text` with `voice` ("" = the engine's default)."""
        self.stop()
        with self._lock:
            generation = self._generation
        self._put(_Item(text, "reply", language, generation, voice or ""))

    def stop(self) -> None:
        """Silence now: drop the queue, flush the audio, close the stream, cancel it in the engine."""
        with self._lock:
            self._generation += 1
            self._replies.clear()
            stream, client = self._stream, self._stream_client
            opening, self._opening = self._opening, None
            self._stream = None
            output, self._output, self._output_format = self._output, None, None
        self._drop_queue()
        if output is not None:
            try:
                output.flush()
                output.close()
            except OSError:
                log.info("voice: flushing the audio output failed", exc_info=True)
        if stream is not None:
            stream.close()
            if client is not None:
                threading.Thread(target=_cancel, args=(client, stream.request_id), daemon=True).start()
        elif opening is not None:
            # Still waiting for the headers (the engine is loading, or the provider is slow): the
            # engine drops the request when it gets the cancel instead of holding the voice busy.
            threading.Thread(target=_cancel, args=opening, daemon=True).start()

    def pause(self) -> None:
        """The microphone is about to record: go quiet now and keep what was being said (and the
        queue) for when the recording ends."""
        with self._lock:
            self._paused_until = self._clock() + PAUSE_GRACE_S
            output = self._output
        if output is None:
            return
        try:
            playing = output.pending()
            output.flush()
        except OSError:
            log.info("voice: pausing the audio output failed", exc_info=True)
            return
        with self._lock:
            self._pause_cut_audio = self._pause_cut_audio or playing

    def close(self) -> None:
        """Quit: stop and end the worker (bounded wait)."""
        self.stop()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is not None:
            self._queue.put(None)
            thread.join(timeout=2.0)

    def voices(self) -> ProviderInfo:
        """BLOCKING: the engine's voice info (for the voice picker). Raises ProviderError."""
        client = self._client_factory()
        if client is None:
            raise ProviderError("unreachable", "no engine")
        return client.info()

    @property
    def reason(self) -> str:
        """Why the engine could not speak the last time it was asked ("" = it could)."""
        with self._lock:
            return self._reason

    # --- worker ---------------------------------------------------------------------------------

    def _drop_queue(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return
            if item is None:  # close() raced with stop(): keep the sentinel
                self._queue.put(None)
                return

    def _current(self, item: _Item) -> bool:
        with self._lock:
            return item.generation == self._generation and not self._closed

    def _holding(self) -> bool:
        with self._lock:
            if self._clock() < self._paused_until:
                return True
        try:
            return self._hold()
        except Exception:  # noqa: BLE001 - a broken hold check must not silence the voice for good
            log.exception("voice: the hold check failed")
            return False

    def _wait_while_held(self, item: _Item) -> None:
        while self._current(item) and self._holding():
            time.sleep(HOLD_POLL_S)

    def _cut_for_hold(self, *, wrote: bool) -> None:
        """A hold began while audio played: silence it. When nothing of the piece being streamed
        was heard yet, the piece before it was still playing: that one is said again too."""
        output = self._output
        playing = False
        if output is not None:
            try:
                playing = output.pending()
                output.flush()
            except OSError:
                log.info("voice: flushing the audio output failed", exc_info=True)
        with self._lock:
            playing = playing or self._pause_cut_audio
            self._pause_cut_audio = False
            if playing and not wrote and self._last_piece is not None:
                self._replay = self._last_piece

    def _take_replay(self) -> _Item | None:
        with self._lock:
            replay, self._replay = self._replay, None
        return replay

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            if not self._current(item):
                continue
            self._wait_while_held(item)
            if not self._current(item):
                continue
            try:
                self._say(item)
            except Exception:  # noqa: BLE001 - one bad line must not end the voice
                log.exception("voice: speaking a line failed")
            if self._queue.empty():
                self._idle()
        self._close_output()

    def _say(self, item: _Item) -> None:
        if self._clock() < self._down_until:
            self._unspoken(item)
            return
        client = self._client_factory()
        if client is None:
            self._unavailable(ProviderError("unreachable", "no engine"), item)
            return
        try:
            info = self._engine_info(client)
            if not self._loaded(client):
                self._unavailable(
                    ProviderError("not_ready", "the engine is loading"), item, retry_after=LOADING_RETRY_S
                )
                return
            text = item.text if info.supports(FEATURE_EXPRESSION_TAGS) else strip_expressions(item.text)
            pieces = split_text(text, info.max_chars or DEFAULT_MAX_CHARS)
            index = 0
            while index < len(pieces):
                self._wait_while_held(item)
                replay = self._take_replay()
                if replay is not None and self._current(replay):
                    self._say_again(client, info, replay)
                    continue  # a hold may have cut the replay too: look again before this piece
                if not self._current(item):
                    return
                outcome = self._speak_piece(client, info, pieces[index], item)
                if outcome == "stop":
                    return
                if outcome == "done":
                    index += 1
        except ProviderError as exc:
            if not self._current(item):
                return  # stopped meanwhile: the broken request is the stop, not an outage
            if exc.kind == "busy":
                log.info("voice: the engine is speaking already, line dropped")
                return
            self._info = None
            if exc.code == "unknown_voice":
                voice = item.voice if item.voice is not None else self._voice()
                log.info("voice: the engine does not know the voice %r", voice)
                self._report(
                    _("the engine has no voice '{voice}'. Pick one in Settings, Assistant").format(voice=voice)
                )
                self._unspoken(item)
                return
            self._unavailable(exc, item)
            return
        self._available()

    def _say_again(self, client: ProviderClient, info: ProviderInfo, piece: _Item) -> None:
        """A piece a hold cut while it played (its text is already split and stripped)."""
        while self._current(piece):
            self._wait_while_held(piece)
            if self._speak_piece(client, info, piece.text, piece) != "held":
                return

    def _engine_info(self, client: ProviderClient) -> ProviderInfo:
        if self._info is None or self._info_url != client.url:
            self._info = client.info()
            self._info_url = client.url
        return self._info

    def _loaded(self, client: ProviderClient) -> bool:
        """False while the engine is still loading its voice; the load is started (once) from here."""
        try:
            if client.health() == "ok":
                return True
        except ProviderError:
            return True  # not knowing is not a reason to stay silent: /v1/speak will say what is wrong
        if not self._warming:
            self._warming = True
            threading.Thread(target=self._warm_up, args=(client,), daemon=True, name="assistant-voice-warmup").start()
        return False

    def _warm_up(self, client: ProviderClient) -> None:
        try:
            client.warmup()  # the engine loads on demand; this may outlast the client's own timeout
        except ProviderError as exc:
            log.debug("voice: warmup: %s", exc.detail)
        finally:
            self._warming = False

    def _open_stream(self, client: ProviderClient, info: ProviderInfo, text: str, item: _Item) -> SpeechStream | None:
        """POST /v1/speak and hand the stream over to `stop()`. None when the line was stopped."""
        voice = item.voice if item.voice is not None else self._voice()
        for attempt in range(BUSY_TRIES):
            request_id = uuid.uuid4().hex
            opening = (client, request_id)
            with self._lock:
                if item.generation != self._generation:
                    return None
                self._opening = opening  # `stop()` can cancel it before the headers arrive
            try:
                stream = client.speak(
                    text,
                    voice=voice or None,
                    audio_format=FORMAT_S16,
                    request_id=request_id,
                    default_rate=info.sample_rate,
                )
            except ProviderError as exc:
                with self._lock:
                    if self._opening is opening:
                        self._opening = None
                if exc.kind == "busy" and attempt + 1 < BUSY_TRIES and self._current(item):
                    time.sleep(BUSY_STEP_S)
                    continue
                raise
            with self._lock:
                if self._opening is opening:
                    self._opening = None
                if item.generation == self._generation:
                    if stream.audio_format != FORMAT_S16:
                        stream.close()
                        raise ProviderError("protocol", f"asked for {FORMAT_S16}, got {stream.audio_format}")
                    self._stream, self._stream_client = stream, client
                    return stream
            stream.close()
            return None
        return None

    def _speak_piece(self, client: ProviderClient, info: ProviderInfo, text: str, item: _Item) -> Outcome:
        """One /v1/speak request, played as it streams. "held": a hold cut it, say it again."""
        stream = self._open_stream(client, info, text, item)
        if stream is None:
            return "stop"
        wrote = False
        try:
            output = self._ensure_output(stream.sample_rate, stream.channels)
            if output is None:
                return "stop"
            frame = 2 * stream.channels
            pending = b""
            for data in stream.chunks():
                if not self._current(item):
                    return "stop"
                if self._holding():
                    self._cut_for_hold(wrote=wrote)
                    _cancel_later(client, stream.request_id)
                    return "held"
                pending += data
                usable = len(pending) - len(pending) % frame
                if usable:
                    output.write(pending[:usable])
                    wrote = True
                    pending = pending[usable:]
            output.commit()
            if not self._current(item):
                return "stop"
            with self._lock:
                self._last_piece = replace(item, text=text)
            return "done"
        finally:
            with self._lock:
                if self._stream is stream:
                    self._stream = None
            stream.close()

    def _ensure_output(self, sample_rate: int, channels: int) -> PcmOutput | None:
        if self._output is not None and self._output_format == (sample_rate, channels):
            return self._output
        self._close_output()
        output = self._output_factory()
        try:
            if output is None:
                raise OSError("no audio player")
            output.open(sample_rate, channels)
        except OSError as exc:
            log.info("voice: no audio output: %s", exc)
            self._report(_("no audio output is available on this computer"))
            return None
        with self._lock:
            self._output, self._output_format = output, (sample_rate, channels)
        return output

    def _idle(self) -> None:
        """Nothing queued: let the audio finish (unless more comes or a stop), then free the device."""
        output = self._output
        if output is None:
            return
        while self._queue.empty() and not self._closed:
            if self._holding():
                self._cut_for_hold(wrote=False)
                self._say_held_tail()
                time.sleep(IDLE_POLL_S)
                continue  # the line said again (if any) plays out before the device is let go
            try:
                if not output.pending():
                    break
            except OSError:
                break
            time.sleep(IDLE_POLL_S)
        if self._queue.empty():
            self._close_output()

    def _say_held_tail(self) -> None:
        """The last line was cut by a hold as it finished playing: say it once the hold ends."""
        replay = self._take_replay()
        if replay is None or not self._current(replay):
            return
        self._wait_while_held(replay)
        client = self._client_factory()
        if client is None or not self._current(replay):
            return
        try:
            self._say_again(client, self._engine_info(client), replay)
        except ProviderError as exc:
            log.info("voice: saying a cut line again failed: %s", exc.detail)

    def _close_output(self) -> None:
        with self._lock:
            output, self._output, self._output_format = self._output, None, None
        if output is not None:
            try:
                output.close()
            except OSError:
                log.info("voice: closing the audio output failed", exc_info=True)

    # --- availability ---------------------------------------------------------------------------

    def _unspoken(self, item: _Item) -> None:
        if item.kind == "notice":
            self._fallback(item.text, item.language)

    def _unavailable(self, error: ProviderError, item: _Item, *, retry_after: float = RETRY_AFTER_S) -> None:
        log.info("voice: the engine cannot speak: %s", error.detail)
        self._down_until = self._clock() + retry_after
        self._report(explain(error))
        self._unspoken(item)

    def _report(self, reason: str) -> None:
        with self._lock:
            first = self._reason != reason
            self._reason = reason
            listeners = list(self._listeners) if first else []
        for listener in listeners:
            try:
                listener(reason)
            except Exception:  # noqa: BLE001 - a listener must not break the voice
                log.exception("voice: unavailable listener failed")

    def _available(self) -> None:
        with self._lock:
            if self._reason:
                log.info("voice: the engine speaks again")
            self._reason = ""


def _cancel_later(client: ProviderClient, request_id: str) -> None:
    threading.Thread(target=_cancel, args=(client, request_id), daemon=True).start()


def _cancel(client: ProviderClient, request_id: str) -> None:
    try:
        client.cancel(request_id)
    except ProviderError as exc:
        log.debug("voice: cancel failed: %s", exc.detail)
