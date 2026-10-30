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

`local` is a voice of this computer (Windows' own voices, `app.companion.win.system_voice`) with
the same calls as a provider client: while the engine cannot speak, every line goes to it
instead, so a fresh install speaks with nothing set up. A voice id starting with
`LOCAL_VOICE_PREFIX` belongs to it; the other ids are the engine's, and each side ignores the
other's (it speaks with its own default voice).

One worker thread; every public method returns quickly and may be called from any thread.
Standard library only (no Qt, no numpy).
"""

from __future__ import annotations

import itertools
import logging
import queue
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import Final, Literal, Protocol

from app.i18n import _
from app.protocol.assistant_reply import strip_expressions
from app.protocol.tts_provider import (
    FEATURE_EXPRESSION_TAGS,
    FORMAT_S16,
    ProviderError,
    ProviderInfo,
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
# The local voice (`local`): its client's url and the prefix of its voice ids.
LOCAL_URL: Final = "windows:"
LOCAL_VOICE_PREFIX: Final = "windows/"

# A sentence ends at . ! ? or an ellipsis (also the CJK marks), maybe followed by quotes or
# brackets, then whitespace. Text without such an end waits for more (or for the end of the reply).
_SENTENCE_END: Final = re.compile(r"[.!?…。！？]+[\"'”’)\]]*(?=\s)")

Kind = Literal["reply", "notice"]
# How one /v1/speak request ended: played, stopped (the line is over) or cut by a hold.
Outcome = Literal["done", "stop", "held"]  # "held": a hold cut it; it waits in the replay list


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


class VoiceStream(Protocol):
    """An audio stream of a voice client (`app.protocol.tts_provider.SpeechStream`)."""

    request_id: str
    sample_rate: int
    channels: int
    audio_format: str

    def chunks(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class VoiceClient(Protocol):
    """What the voice asks of a provider (`app.protocol.tts_provider.ProviderClient`, the local
    voice)."""

    url: str

    def info(self) -> ProviderInfo: ...

    def health(self) -> str: ...

    def warmup(self) -> None: ...

    def cancel(self, request_id: str) -> None: ...

    def speak(
        self, text: str, *, voice: str | None, audio_format: str, request_id: str, default_rate: int
    ) -> VoiceStream: ...


ClientFactory = Callable[[], VoiceClient | None]
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
    local: bool = False  # said by the local voice (a piece said again goes back to it)
    client: VoiceClient | None = field(default=None, compare=False)  # the Test button's source
    order: int = field(default=-1, compare=False)  # a piece's place among all played (-1: not played yet)


@dataclass(eq=False)  # identity: two plays of the same text are two entries
class _InDevice:
    """A piece handed to the audio device and when its last byte finishes playing."""

    piece: _Item
    ends: float = 0.0  # time.monotonic(): the device plays in real time, not on the injected clock
    complete: bool = False  # its whole stream reached the device (the engine got to its end)


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
        local: ClientFactory = lambda: None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_factory = client
        self._local = local
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
        self._stream: VoiceStream | None = None
        self._stream_client: VoiceClient | None = None
        self._opening: tuple[VoiceClient, str] | None = None  # a /v1/speak still waiting for its headers
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
        # What the device holds, in order: the engine runs ahead of playback, so several pieces
        # may wait there behind the one being heard.
        self._in_device: list[_InDevice] = []
        self._device_until = 0.0
        self._replay: list[_Item] = []  # pieces a hold cut before they were heard to their end
        self._order = itertools.count()  # numbers each new piece as it starts playing

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

    def test(self, text: str, voice: str, language: str = "", client: VoiceClient | None = None) -> None:
        """Stop what is being said and say `text` with `voice` ("" = the default voice), through
        `client` when given (a source picked but not saved yet), else the way replies go."""
        self.stop()
        with self._lock:
            generation = self._generation
        self._put(_Item(text, "reply", language, generation, voice or "", client=client))

    def stop(self) -> None:
        """Silence now: drop the queue, flush the audio, close the stream, cancel it in the engine."""
        with self._lock:
            self._generation += 1
            self._replies.clear()
            stream, client = self._stream, self._stream_client
            opening, self._opening = self._opening, None
            self._stream = None
            output, self._output, self._output_format = self._output, None, None
            self._in_device.clear()
            self._device_until = 0.0
            self._replay.clear()
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
        self._cut_for_hold()

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
        if self._current(item) and self._holding():
            self._cut_for_hold()  # the device may still play earlier pieces: not over the microphone
        while self._current(item) and self._holding():
            time.sleep(HOLD_POLL_S)

    def _cut_for_hold(self) -> None:
        """A hold began: silence the device. Every piece it had not finished playing, the one
        still streaming included, is said again once the hold ends, in the order it was played."""
        with self._lock:
            now = time.monotonic()
            unheard = [entry.piece for entry in self._in_device if not entry.complete or entry.ends > now]
            # In the order first played: a piece listed just after an earlier cut (it was opening
            # its stream) goes behind the ones that cut moved, a piece said again keeps its place.
            self._replay = sorted(self._replay + unheard, key=lambda piece: piece.order)
            self._in_device.clear()
            self._device_until = 0.0
            output = self._output  # decided before the flush: what plays after it was not cut
        if output is not None:
            try:
                output.flush()
            except OSError:
                log.info("voice: flushing the audio output failed", exc_info=True)

    def _note_write(self, entry: _InDevice, size: int, rate: int, channels: int) -> None:
        """`size` bytes of `entry`'s piece went to the device: when they finish playing."""
        with self._lock:
            now = time.monotonic()
            self._device_until = max(self._device_until, now) + size / (rate * 2 * channels)
            entry.ends = self._device_until  # after a cut it is in the replay list: harmless
            # Pieces heard to their end are history.
            self._in_device = [e for e in self._in_device if e.ends > now or not e.complete]

    def _take_replay(self) -> _Item | None:
        with self._lock:
            return self._replay.pop(0) if self._replay else None

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
        if item.client is not None:
            try:
                self._say_with(item.client, item)
            except ProviderError as exc:
                self._info = None
                if self._current(item):
                    log.info("voice: the test line failed: %s", exc.detail)
                    self._report(explain(exc))
            return
        if self._clock() >= self._down_until:
            error = self._say_on_engine(item)
            if error is None:
                return
            self._down(error)
        if not self._say_locally(item):
            self._unspoken(item)

    def _say_on_engine(self, item: _Item) -> tuple[ProviderError, float] | None:
        """None when the line is over (said, stopped or dropped); else why the engine cannot
        speak and when to ask it again."""
        client = self._client_factory()
        if client is None:
            return ProviderError("unreachable", "no engine"), RETRY_AFTER_S
        try:
            if not self._loaded(client):
                return ProviderError("not_ready", "the engine is loading"), LOADING_RETRY_S
            self._say_with(client, item)
        except ProviderError as exc:
            if not self._current(item):
                return None  # stopped meanwhile: the broken request is the stop, not an outage
            if exc.kind == "busy":
                log.info("voice: the engine is speaking already, line dropped")
                return None
            self._info = None
            if exc.code == "unknown_voice":
                voice = item.voice if item.voice is not None else self._voice()
                log.info("voice: the engine does not know the voice %r", voice)
                self._report(
                    _("the engine has no voice '{voice}'. Pick one in Settings, Assistant").format(voice=voice)
                )
                self._unspoken(item)
                return None
            return exc, RETRY_AFTER_S
        self._available()
        return None

    def _say_locally(self, item: _Item) -> bool:
        """The engine cannot speak: the local voice says the line. False when there is none or
        it failed (the line stays unspoken)."""
        client = self._local()
        if client is None:
            return False
        try:
            self._say_with(client, item)
        except ProviderError as exc:
            self._info = None
            if not self._current(item):
                return True
            log.info("voice: the local voice failed: %s", exc.detail)
            self._report(explain(exc))
            return False
        return True

    def _say_with(self, client: VoiceClient, item: _Item) -> None:
        """Every piece of `item` through `client`, after whatever a hold cut. Raises ProviderError."""
        item = replace(item, local=client.url == LOCAL_URL)
        info = self._engine_info(client)
        text = item.text if info.supports(FEATURE_EXPRESSION_TAGS) else strip_expressions(item.text)
        pieces = split_text(text, info.max_chars or DEFAULT_MAX_CHARS)
        index = 0
        while index < len(pieces):
            self._wait_while_held(item)
            replay = self._take_replay()
            if replay is not None:
                if self._current(replay):
                    self._say_again(replay)
                continue  # the replay list goes first, in order: look again before this piece
            if not self._current(item):
                return
            outcome = self._speak_piece(client, info, pieces[index], item)
            if outcome == "stop":
                return
            index += 1  # "held" too: the cut put it in the replay list, said at the top of the loop

    def _client_for(self, piece: _Item) -> VoiceClient | None:
        """The client that said `piece` before (to say it again)."""
        if piece.client is not None:
            return piece.client
        client = self._client_factory()
        if piece.local and (client is None or client.url != LOCAL_URL):
            return self._local()
        return client

    def _say_again(self, piece: _Item) -> None:
        """A piece a hold cut before it was heard (its text is already split and stripped).
        Raises ProviderError."""
        client = self._client_for(piece)
        if client is None:
            return
        info = self._engine_info(client)
        self._wait_while_held(piece)
        if self._current(piece):
            self._speak_piece(client, info, piece.text, piece)  # cut again: back in the replay list

    def _engine_info(self, client: VoiceClient) -> ProviderInfo:
        if self._info is None or self._info_url != client.url:
            self._info = client.info()
            self._info_url = client.url
        return self._info

    def _loaded(self, client: VoiceClient) -> bool:
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

    def _warm_up(self, client: VoiceClient) -> None:
        try:
            client.warmup()  # the engine loads on demand; this may outlast the client's own timeout
        except ProviderError as exc:
            log.debug("voice: warmup: %s", exc.detail)
        finally:
            self._warming = False

    def _open_stream(self, client: VoiceClient, info: ProviderInfo, text: str, item: _Item) -> VoiceStream | None:
        """POST /v1/speak and hand the stream over to `stop()`. None when the line was stopped."""
        voice = item.voice if item.voice is not None else self._voice()
        if voice.startswith(LOCAL_VOICE_PREFIX) != item.local:
            voice = ""  # a voice of the other side: this one speaks with its own default
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

    def _speak_piece(self, client: VoiceClient, info: ProviderInfo, text: str, item: _Item) -> Outcome:
        """One /v1/speak request, played as it streams. "held": a hold cut it, and it waits in the
        replay list with whatever else the device had not played."""
        stream = self._open_stream(client, info, text, item)
        if stream is None:
            return "stop"
        piece = replace(item, text=text)
        entry = _InDevice(piece if piece.order >= 0 else replace(piece, order=next(self._order)))
        try:
            output = self._ensure_output(stream.sample_rate, stream.channels)
            if output is None:
                return "stop"
            with self._lock:
                # Listed before its first byte: a cut from here on (`pause()` included) keeps it.
                self._in_device.append(entry)
            frame = 2 * stream.channels
            pending = b""
            for data in stream.chunks():
                if not self._current(item):
                    return "stop"
                if self._holding():
                    self._cut_for_hold()
                    _cancel_later(client, stream.request_id)
                    return "held"
                pending += data
                usable = len(pending) - len(pending) % frame
                if usable:
                    output.write(pending[:usable])
                    self._note_write(entry, usable, stream.sample_rate, stream.channels)
                    pending = pending[usable:]
            output.commit()
            if not self._current(item):
                return "stop"
            with self._lock:
                entry.complete = True
                cut = entry not in self._in_device  # a cut while it streamed: in the replay list
            if self._holding():  # began as the stream ended: the cut must see this piece
                self._cut_for_hold()
                return "held"
            return "held" if cut else "done"
        finally:
            with self._lock:
                # Stopped or failed before its end (a stop() can land while the device opens):
                # nothing to say again, and no later cut may find it.
                if not entry.complete and entry in self._in_device:
                    self._in_device.remove(entry)
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
        while self._queue.empty() and not self._closed:
            if self._holding():
                self._cut_for_hold()
                with self._lock:
                    nothing_to_say = not self._replay
                if nothing_to_say:
                    break  # the device is let go for the recording
                self._say_held_tail()
                continue  # what was said again plays out before the device is let go
            output = self._output  # saying a line again may have reopened it in another format
            if output is None:
                break
            try:
                if not output.pending():
                    break
            except OSError:
                break
            time.sleep(IDLE_POLL_S)
        if self._queue.empty():
            self._close_output()

    def _say_held_tail(self) -> None:
        """The last lines were cut by a hold before they were heard: say them once it ends."""
        while (replay := self._take_replay()) is not None:
            if not self._current(replay):
                continue
            self._wait_while_held(replay)
            if not self._current(replay):
                continue
            try:
                self._say_again(replay)
            except ProviderError as exc:
                log.info("voice: saying a cut line again failed: %s", exc.detail)
                return

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

    def _down(self, error: tuple[ProviderError, float]) -> None:
        """The engine cannot speak: ask it again after a while. Listeners hear why only when no
        local voice takes over (with one, the replies are still spoken)."""
        exc, retry_after = error
        log.info("voice: the engine cannot speak: %s", exc.detail)
        self._down_until = self._clock() + retry_after
        if self._local() is None:
            self._report(explain(exc))

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


def _cancel_later(client: VoiceClient, request_id: str) -> None:
    threading.Thread(target=_cancel, args=(client, request_id), daemon=True).start()


def _cancel(client: VoiceClient, request_id: str) -> None:
    try:
        client.cancel(request_id)
    except ProviderError as exc:
        log.debug("voice: cancel failed: %s", exc.detail)
