import sys
import threading
import time
import traceback

import numpy as np
from numpy.typing import NDArray

from app.core.audio_feedback import AudioFeedback
from app.core.config import Config
from app.core.recorder import Recorder
from app.core.session_status import SessionState, SessionStatus, ToggleAction, ToggleOutcome
from app.core.transcription_backend import TranscriptionBackend
from app.core.transcription_handler import TranscriptionHandler
from app.i18n import _


class RecordingSession:
    """Manage the recording → transcription → handler cycle with a state machine.

    States: idle → recording → processing → idle. A trigger in `processing`
    cancels the active handler and starts a new recording immediately.
    The `handler_id` passed to `toggle()` is only used when the trigger
    STOPS the recording (it decides the text's destination).

    `toggle()` returns a `ToggleOutcome` immediately (the start/stop decision is
    synchronous under the lock — only the transcription is asynchronous), so the
    trigger knows what happened. If a `SessionStatus` is injected, the state
    transitions are published to it (live state queryable by the consumers).
    """

    def __init__(
        self,
        recorder: Recorder,
        transcriber: TranscriptionBackend,
        audio: AudioFeedback,
        config: Config,
        handlers: dict[str, TranscriptionHandler],
        default_handler_id: str = "clipboard",
        status: SessionStatus | None = None,
    ) -> None:
        if not handlers:
            raise ValueError("RecordingSession needs at least one handler")
        if default_handler_id not in handlers:
            raise ValueError(f"default_handler_id '{default_handler_id}' is not in handlers")
        self._recorder = recorder
        self._transcriber = transcriber
        self._audio = audio
        self._sample_rate = config.sample_rate
        self._max_seconds = config.max_recording_seconds
        self._warning_percent = config.timeout_warning_percent
        self._handlers = handlers
        self._default_handler_id = default_handler_id
        self._status = status
        self._lock = threading.Lock()
        self._warning_timer: threading.Timer | None = None
        self._timeout_timer: threading.Timer | None = None
        self._state: SessionState = "idle"
        self._stop_handler_id: str | None = None
        self._active_handler_id: str | None = None
        self._slow_warning_shown = False
        # op_seq: the session is the authority. Each recording that STARTS opens a
        # new operation; the STOP continues the same one (it goes to processing).
        self._op_counter = 0
        self._op_seq = 0
        self._op_flow: str | None = None
        self._op_client_id: str | None = None

    def toggle(self, handler_id: str, client_id: str | None = None) -> ToggleOutcome | None:
        if handler_id not in self._handlers:
            print(_("[VoiceMate] ⚠ Unknown handler: {handler_id}").format(handler_id=handler_id))
            return None
        handler_to_cancel: TranscriptionHandler | None = None
        outcome: ToggleOutcome | None = None
        with self._lock:
            state = self._state
            if state == "idle":
                self._start_locked(handler_id, client_id)
                outcome = self._outcome_locked("started")
            elif state == "recording":
                self._stop_handler_id = handler_id
                self._cancel_timers()
                self._state = "processing"
                self._publish_state_locked("processing")
                outcome = self._outcome_locked("stopped")
                threading.Thread(target=self._stop_and_dispatch, daemon=True).start()
            elif state == "processing":
                active = self._active_handler_id
                if active is not None:
                    handler_to_cancel = self._handlers[active]
                self._active_handler_id = None
        if state == "processing":
            # Release the lock before calling cancel_in_flight (which may block
            # briefly on I/O) and before starting the recorder.
            if handler_to_cancel is not None:
                handler_to_cancel.cancel_in_flight()
            with self._lock:
                if self._state == "processing":
                    self._start_locked(handler_id, client_id)
                    outcome = self._outcome_locked("restarted")
                else:
                    outcome = self._outcome_locked("started")
        return outcome

    def _outcome_locked(self, action: ToggleAction) -> ToggleOutcome:
        return ToggleOutcome(
            action=action,
            op_seq=self._op_seq,
            state=self._state,
            flow=self._op_flow or self._default_handler_id,
        )

    def _publish_state_locked(self, state: SessionState) -> None:
        if self._status is not None:
            self._status.set_operation(self._op_seq, state, self._op_flow, self._op_client_id)

    def _start_locked(self, handler_id: str, client_id: str | None) -> None:
        if not self._recorder.start():
            self._state = "idle"
            return
        self._state = "recording"
        self._op_counter += 1
        self._op_seq = self._op_counter
        self._op_flow = handler_id
        self._op_client_id = client_id
        self._publish_state_locked("recording")
        self._audio.recording_started()
        print(_("[VoiceMate] 🎙  Recording... (press to stop)"))
        self._schedule_timers_locked()

    def _schedule_timers_locked(self) -> None:
        warning_at = self._max_seconds * self._warning_percent
        self._warning_timer = threading.Timer(warning_at, self._on_warning)
        self._warning_timer.daemon = True
        self._warning_timer.start()
        self._timeout_timer = threading.Timer(float(self._max_seconds), self._on_timeout)
        self._timeout_timer.daemon = True
        self._timeout_timer.start()

    def _cancel_timers(self) -> None:
        if self._warning_timer is not None:
            self._warning_timer.cancel()
            self._warning_timer = None
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

    def _on_warning(self) -> None:
        remaining = self._max_seconds * (1 - self._warning_percent)
        print(_("[VoiceMate] ⚠ Recording will end in {remaining:.0f}s").format(remaining=remaining))
        self._audio.timeout_warning()

    def _on_timeout(self) -> None:
        print(_("[VoiceMate] ⏰ Maximum time reached. Ending recording..."))
        with self._lock:
            if self._state != "recording":
                return
            self._cancel_timers()
            self._stop_handler_id = self._default_handler_id
            self._state = "processing"
            self._publish_state_locked("processing")
        self._stop_and_dispatch()

    def _stop_and_dispatch(self) -> None:
        """Stop the recording, transcribe and dispatch — runs in its own thread.

        Any exception here is logged (traceback) and the state ALWAYS returns to
        idle: a daemon thread that dies silently would leave the session stuck in
        `processing` and the toggle "dead" with no clue in the log.
        """
        stop_id: str | None = None
        try:
            result: NDArray[np.float32] | None = self._recorder.stop()
            with self._lock:
                stop_id = self._stop_handler_id
                self._stop_handler_id = None
                if self._state != "processing":
                    # User cancelled and started a new recording; we abort silently.
                    return
                if stop_id is None:
                    self._state = "idle"
                    return
                self._active_handler_id = stop_id

            if result is None:
                print(_("[VoiceMate] No audio captured."))
                return

            duration = len(result) / self._sample_rate
            print(_("[VoiceMate] ⏳ Transcribing {duration:.1f}s of audio...").format(duration=duration))
            started_at = time.perf_counter()
            text = self._transcriber.transcribe(result)
            self._warn_if_slow(duration, time.perf_counter() - started_at)
            if not text:
                print(_("[VoiceMate] No speech detected."))
                return

            self._handlers[stop_id].handle(text)
        except Exception:  # noqa: BLE001 — thread boundary: log + recover
            print(_("[VoiceMate] ❌ Error processing the recording:"), file=sys.stderr)
            traceback.print_exc()
            try:
                self._audio.error()
            except Exception:  # noqa: BLE001, S110 — beep is best-effort
                pass
        finally:
            if stop_id is not None:
                self._finish_processing_locked(stop_id)

    def _warn_if_slow(self, audio_seconds: float, elapsed: float) -> None:
        """Detect a GPU-less backend (silent fallback to CPU/software).

        Healthy GPU transcription runs well below real time; 3× the audio
        duration (with a 5s floor to absorb warmup) indicates the backend is
        running on CPU/Vulkan-software. Warns once per session.
        """
        if self._slow_warning_shown or elapsed <= max(5.0, 3.0 * audio_seconds):
            return
        self._slow_warning_shown = True
        ratio = elapsed / audio_seconds if audio_seconds > 0 else float("inf")
        print(
            _(
                "[VoiceMate] ⚠ Transcription {ratio:.0f}× slower than the audio "
                "({elapsed:.0f}s for {audio_seconds:.0f}s) — the backend is probably running WITHOUT GPU. "
                "Run `make doctor` for diagnosis."
            ).format(ratio=ratio, elapsed=elapsed, audio_seconds=audio_seconds),
            file=sys.stderr,
        )

    def _finish_processing_locked(self, stop_id: str) -> None:
        op_seq: int | None = None
        with self._lock:
            if self._state == "processing" and self._active_handler_id == stop_id:
                self._state = "idle"
                self._active_handler_id = None
                op_seq = self._op_seq
        # Publish idle OUTSIDE the session lock (the hub has its own lock) and only
        # if this finalization is the one that actually returned to idle — mark_idle
        # ignores it if a new operation already opened on top (race-free).
        if op_seq is not None and self._status is not None:
            self._status.mark_idle(op_seq)
