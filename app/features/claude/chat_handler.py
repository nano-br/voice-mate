from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
import time
from collections.abc import Callable
from types import TracebackType

from app.core.audio_feedback import AudioFeedback
from app.core.chat import ChatBackend
from app.core.session_status import OperationHandle
from app.features.claude.sentence_buffer import SentenceBuffer
from app.features.tts.base import TextToSpeech
from app.i18n import _
from app.platform.clipboard import ClipboardWriter, PyperclipWriter
from app.protocol.models import Phase

_HEARTBEAT_INTERVAL = 3.0


class _Heartbeat:
    """Prints 'still processing (Ns)' periodically until stopped.

    On the non-streaming path (no TTS) Claude blocks until the whole response
    arrives; without this the user is left in an "undefined status", unsure
    whether it hung.
    """

    def __init__(self, interval: float = _HEARTBEAT_INTERVAL) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start = 0.0

    def __enter__(self) -> _Heartbeat:
        self._start = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ClaudeHeartbeat")
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            elapsed = time.monotonic() - self._start
            print(_("[VoiceMate] ⏳ Claude processing... ({elapsed:.0f}s)").format(elapsed=elapsed))


class _PhaseOnce:
    """Moves an operation to `phase` the first time it is called."""

    def __init__(self, op: OperationHandle, phase: Phase) -> None:
        self._op = op
        self._phase: Phase = phase
        self._done = False

    def __call__(self) -> None:
        if not self._done:
            self._done = True
            self._op.set_phase(self._phase)


class ClaudeChatHandler:
    """Sends the transcribed text to Claude and copies the response to the clipboard.

    Depends on any `ChatBackend` (Protocol). `ClaudeRuntime` is the default
    backend today, but others (Codex, Antigravity, etc.) can be injected with
    no change here. If an active `TextToSpeech` is injected, the response is
    also spoken via TTS (and the traditional beep is suppressed). Supports
    cancelling the in-flight turn via `cancel_in_flight()` — it interrupts the
    AI call and the TTS playback simultaneously. `timeout_seconds` is
    defense-in-depth against a backend hang.
    """

    def __init__(
        self,
        runtime: ChatBackend,
        audio: AudioFeedback,
        speaker: TextToSpeech,
        timeout_seconds: float = 120.0,
        clipboard: ClipboardWriter | None = None,
    ) -> None:
        self._runtime = runtime
        self._audio = audio
        self._speaker = speaker
        self._timeout_seconds = timeout_seconds
        self._clipboard: ClipboardWriter = clipboard if clipboard is not None else PyperclipWriter()
        self._lock = threading.Lock()
        self._busy = False
        self._cancelled = False

    def handle(self, text: str, op: OperationHandle | None = None) -> None:
        operation = op if op is not None else OperationHandle()
        with self._lock:
            self._busy = True
            self._cancelled = False
        try:
            transcription_preview = text[:200] + ("..." if len(text) > 200 else "")
            print(_("[VoiceMate] ✓ Transcription: {preview}").format(preview=transcription_preview))
            # A restart may have taken over between the transcription and this call: the
            # transcript is still delivered (it is the user's words), but Claude is not
            # called, and the transcript becomes the operation's last (final) result.
            proceed = operation.active()
            publication = operation.publish_result(text, "transcript", final=not proceed)
            if publication.write_clipboard:
                self._clipboard.copy(text)
                print(_("[VoiceMate] 📋 Transcription copied to clipboard."))
            elif publication.published:
                print(_("[VoiceMate] 📋 The clipboard lease holder copies it to the clipboard."))
            if not proceed:
                print(_("[VoiceMate] Claude not called: the operation was cancelled or a new recording took over."))
                return
            self._ask(text, operation)
        finally:
            with self._lock:
                self._busy = False
                self._cancelled = False

    def _ask(self, text: str, op: OperationHandle) -> None:
        if not op.active():
            return
        op.set_phase("thinking")
        try:
            print(_("[VoiceMate] 🤖 Calling Claude..."))
            started = time.monotonic()
            # With TTS active: sentence-by-sentence streaming (speaks the 1st
            # sentence before the response finishes). Without TTS: full collect +
            # heartbeat (so the user isn't left without feedback while Claude thinks).
            spoken = self._speaker.is_active()
            if spoken:
                response = self._stream_and_speak(text, op)
                spoken = spoken and self._speaker.is_active()  # the TTS may have died mid-answer
            else:
                with _Heartbeat():
                    response = self._runtime.send_and_collect(text, timeout=self._timeout_seconds)
            if self._is_cancelled() or not op.active():
                print(_("[VoiceMate] ✗ Claude response discarded (cancelled)."))
                return
            if not response:
                print(_("[VoiceMate] Claude returned an empty response."))
                return
            response_preview = response[:200] + ("..." if len(response) > 200 else "")
            print(_("[VoiceMate] 💬 Claude: {preview}").format(preview=response_preview))
            print(_("[VoiceMate] ⏱ Full response in {elapsed:.1f}s").format(elapsed=time.monotonic() - started))
            publication = op.publish_result(response, "ai_response", final=True, spoken=spoken)
            if publication.write_clipboard:
                self._clipboard.copy(response)
                print(_("[VoiceMate] 📋 Claude response copied to clipboard."))
            elif publication.published:
                print(_("[VoiceMate] 📋 The clipboard lease holder copies it to the clipboard."))
            if publication.play_cue and not spoken:
                self._audio.ai_response_ready()
        except asyncio.CancelledError:
            print(_("[VoiceMate] ✗ Claude call cancelled."))
        except (TimeoutError, concurrent.futures.TimeoutError):
            print(
                _("[VoiceMate] ⏱ Claude exceeded timeout ({timeout:.0f}s), discarding turn.").format(
                    timeout=self._timeout_seconds
                ),
                file=sys.stderr,
            )
            self._runtime.interrupt()
            if op.publish_error(
                "chat_failed",
                detail=f"no answer within {self._timeout_seconds:.0f}s",
                message=_("Claude did not answer within {timeout:.0f}s.").format(timeout=self._timeout_seconds),
            ):
                self._audio.error()
        except Exception as exc:  # noqa: BLE001
            print(_("[VoiceMate] ❌ Error talking to Claude: {exc}").format(exc=exc), file=sys.stderr)
            if op.publish_error(
                "chat_failed",
                detail=str(exc) or type(exc).__name__,
                message=_("Error talking to Claude: {detail}").format(detail=exc),
            ):
                self._audio.error()

    def _stream_and_speak(self, text: str, op: OperationHandle) -> str:
        """Consume Claude's stream, speak sentence by sentence, and return the full text.

        `speak()` enqueues each sentence on the persistent player and returns; the
        audio plays while the next sentence is generated (pipeline, no gaps).
        `wait_done()` at the end waits for playback to finish. The full text is
        accumulated for the clipboard. The phase turns to `speaking` with the
        first sentence handed to the TTS.
        """
        buffer = SentenceBuffer()
        parts: list[str] = []
        first_token = True
        on_speak = _PhaseOnce(op, "speaking")
        for delta in self._runtime.stream(text, timeout=self._timeout_seconds):
            if self._is_cancelled():
                break
            if first_token and delta:
                # Signals that Claude started responding (before, only the final
                # response showed up — the user was left unsure if it had hung).
                print(_("[VoiceMate] 💬 Claude responding..."))
                first_token = False
            parts.append(delta)
            if not self._speak_all(buffer.feed(delta), on_speak):
                break
        if not self._is_cancelled():
            tail = buffer.flush()
            if tail:
                self._speak_all([tail], on_speak)
            self._speaker.wait_done()
        return "".join(parts)

    def _speak_all(self, sentences: list[str], on_speak: Callable[[], None]) -> bool:
        """Speak each sentence in order; returns False if cancelled midway."""
        for sentence in sentences:
            if self._is_cancelled():
                return False
            on_speak()
            self._speaker.speak(sentence)
        return True

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def cancel_in_flight(self) -> None:
        with self._lock:
            if not self._busy:
                return
            self._cancelled = True
        self._runtime.interrupt()
        self._speaker.stop()

    def close(self) -> None:
        self._runtime.stop()
        self._speaker.close()
