from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading
import time
from types import TracebackType

from app.core.audio_feedback import AudioFeedback
from app.core.chat import ChatBackend
from app.features.claude.sentence_buffer import SentenceBuffer
from app.features.tts.base import TextToSpeech
from app.i18n import _
from app.platform.clipboard import ClipboardWriter, PyperclipWriter

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

    def handle(self, text: str) -> None:
        with self._lock:
            self._busy = True
            self._cancelled = False
        transcription_preview = text[:200] + ("..." if len(text) > 200 else "")
        print(_("[VoiceMate] ✓ Transcription: {preview}").format(preview=transcription_preview))
        self._clipboard.copy(text)
        print(_("[VoiceMate] 📋 Transcription copied to clipboard."))
        try:
            print(_("[VoiceMate] 🤖 Calling Claude..."))
            started = time.monotonic()
            # With TTS active: sentence-by-sentence streaming (speaks the 1st
            # sentence before the response finishes). Without TTS: full collect +
            # heartbeat (so the user isn't left without feedback while Claude thinks).
            if self._speaker.is_active():
                response = self._stream_and_speak(text)
            else:
                with _Heartbeat():
                    response = self._runtime.send_and_collect(text, timeout=self._timeout_seconds)
            if self._is_cancelled():
                print(_("[VoiceMate] ✗ Claude response discarded (cancelled)."))
                return
            if not response:
                print(_("[VoiceMate] Claude returned an empty response."))
                return
            response_preview = response[:200] + ("..." if len(response) > 200 else "")
            print(_("[VoiceMate] 💬 Claude: {preview}").format(preview=response_preview))
            print(_("[VoiceMate] ⏱ Full response in {elapsed:.1f}s").format(elapsed=time.monotonic() - started))
            self._clipboard.copy(response)
            print(_("[VoiceMate] 📋 Claude response copied to clipboard."))
            if not self._speaker.is_active():
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
            self._audio.error()
        except Exception as exc:  # noqa: BLE001
            print(_("[VoiceMate] ❌ Error talking to Claude: {exc}").format(exc=exc), file=sys.stderr)
            self._audio.error()
        finally:
            with self._lock:
                self._busy = False
                self._cancelled = False

    def _stream_and_speak(self, text: str) -> str:
        """Consume Claude's stream, speak sentence by sentence, and return the full text.

        `speak()` enqueues each sentence on the persistent player and returns; the
        audio plays while the next sentence is generated (pipeline, no gaps).
        `wait_done()` at the end waits for playback to finish. The full text is
        accumulated for the clipboard.
        """
        buffer = SentenceBuffer()
        parts: list[str] = []
        first_token = True
        for delta in self._runtime.stream(text, timeout=self._timeout_seconds):
            if self._is_cancelled():
                break
            if first_token and delta:
                # Signals that Claude started responding (before, only the final
                # response showed up — the user was left unsure if it had hung).
                print(_("[VoiceMate] 💬 Claude responding..."))
                first_token = False
            parts.append(delta)
            if not self._speak_all(buffer.feed(delta)):
                break
        if not self._is_cancelled():
            tail = buffer.flush()
            if tail:
                self._speaker.speak(tail)
            self._speaker.wait_done()
        return "".join(parts)

    def _speak_all(self, sentences: list[str]) -> bool:
        """Speak each sentence in order; returns False if cancelled midway."""
        for sentence in sentences:
            if self._is_cancelled():
                return False
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
