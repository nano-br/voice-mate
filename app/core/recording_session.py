import sys
import threading
import time
import traceback
from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from app.core.audio_feedback import AudioFeedback
from app.core.config import Config
from app.core.recorder import Recorder
from app.core.session_status import CancelOutcome, OperationHandle, SessionState, SessionStatus, ToggleOutcome
from app.core.transcription_backend import TranscriptionBackend
from app.core.transcription_handler import TranscriptionHandler
from app.i18n import _
from app.protocol.models import FlowKind, TriggerAction, TriggerExpect, WarningCode


def _describe(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= 300 else text[:299] + "…"


class RecordingSession:
    """Manage the recording -> transcription -> handler cycle with a state machine.

    States: idle -> recording -> processing -> idle. The `handler_id` passed to
    `toggle()` only matters when the trigger STOPS the recording (it decides the
    text's destination). A trigger in `processing` restarts: the old operation
    is superseded (its pending AI work is cancelled, a transcript already being
    produced is still published) and a new recording starts at once.
    `cancel()` drops the current operation entirely: nothing more is published
    for it.

    `toggle()` returns a `ToggleOutcome` immediately (the start/stop decision is
    synchronous under the lock; only the transcription is asynchronous), so the
    trigger knows what happened. If a `SessionStatus` is injected, every state
    and phase change, result, warning and error is published to it, and the
    session only plays the cues that no client took over (the `cues` lease).
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
        flow_kinds: Mapping[str, FlowKind] | None = None,
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
        self._flow_kinds: dict[str, FlowKind] = dict(flow_kinds or {})
        self._lock = threading.Lock()
        self._warning_timer: threading.Timer | None = None
        self._timeout_timer: threading.Timer | None = None
        self._state: SessionState = "idle"
        self._active_handler_id: str | None = None
        self._slow_warning_shown = False
        # Single-flight error beep: with a dead audio server a beep hangs forever,
        # so a new one is skipped while the previous is still stuck.
        self._error_beep_busy = threading.Event()
        # op_seq: the session is the authority. Each recording that STARTS opens a
        # new operation; the STOP continues the same one (it goes to processing).
        self._op_counter = 0
        self._op_seq = 0
        # The flow that started the recording while recording; the destination flow
        # (whose trigger stopped it) while processing.
        self._op_flow: str | None = None
        self._op_client_id: str | None = None
        # Set when a restart or a cancel takes over the operation.
        self._op_superseded = threading.Event()

    # -- triggers --------------------------------------------------------------
    def toggle(
        self, handler_id: str, client_id: str | None = None, expect: TriggerExpect = "toggle"
    ) -> ToggleOutcome | None:
        """Act like pressing `handler_id`'s hotkey. `expect` makes it idempotent for clients that
        know what they want: `start` never stops and `stop` never starts (`noop` instead)."""
        if handler_id not in self._handlers:
            print(_("[VoiceMate] ⚠ Unknown handler: {handler_id}").format(handler_id=handler_id))
            return None
        with self._lock:
            state = self._state
            if state == "idle":
                if expect == "stop":
                    return self._outcome_locked("noop", handler_id)
                self._start_locked(handler_id, client_id)
                return self._outcome_locked("started", handler_id)
            if state == "recording":
                if expect == "start":
                    return self._outcome_locked("noop", handler_id)
                self._stop_locked(handler_id)
                return self._outcome_locked("stopped", handler_id)
            if expect == "stop":
                return self._outcome_locked("noop", handler_id)
            handler_to_cancel = self._supersede_locked()
        # Release the lock before calling cancel_in_flight (which may block
        # briefly on I/O) and before starting the recorder.
        if handler_to_cancel is not None:
            handler_to_cancel.cancel_in_flight()
        with self._lock:
            if self._state == "processing":
                self._start_locked(handler_id, client_id)
                return self._outcome_locked("restarted", handler_id)
            if self._state == "idle":  # the old operation finished meanwhile
                self._start_locked(handler_id, client_id)
                return self._outcome_locked("started", handler_id)
            return self._outcome_locked("noop", handler_id)  # another trigger started one meanwhile

    def cancel(self, op_seq: int | None = None) -> CancelOutcome:
        """Drop the current operation (only if it is `op_seq`, when given): no result is published."""
        stop_recorder = False
        handler_to_cancel: TranscriptionHandler | None = None
        with self._lock:
            if self._state == "idle" or (op_seq is not None and op_seq != self._op_seq):
                return CancelOutcome(action="noop", state=self._state)
            current = self._op_seq
            if self._state == "recording":
                self._cancel_timers()
                stop_recorder = True
            else:
                handler_to_cancel = self._supersede_locked()
            self._op_superseded.set()
            if self._status is not None:
                self._status.cancel_operation(current)
            self._state = "idle"
        print(_("[VoiceMate] ✗ Operation {op_seq} cancelled.").format(op_seq=current))
        if self._status is not None:
            self._status.mark_idle(current)
        if stop_recorder:
            try:
                self._recorder.stop()  # the captured audio is discarded
            except Exception as exc:  # noqa: BLE001 (the session is idle either way)
                print(_("[VoiceMate] ⚠ Could not stop the microphone: {exc}").format(exc=exc), file=sys.stderr)
        if handler_to_cancel is not None:
            handler_to_cancel.cancel_in_flight()
        return CancelOutcome(action="cancelled", state="idle")

    # -- state machine (lock held) ---------------------------------------------
    def _outcome_locked(self, action: TriggerAction, requested: str) -> ToggleOutcome:
        return ToggleOutcome(action=action, op_seq=self._op_seq, state=self._state, flow=requested)

    def _flow_kind(self, flow: str | None) -> FlowKind | None:
        if flow is None:
            return None
        return self._flow_kinds.get(flow)

    def _publish_state_locked(self) -> None:
        if self._status is None:
            return
        self._status.set_operation(
            self._op_seq,
            self._state,
            self._op_flow,
            self._op_client_id,
            flow_kind=self._flow_kind(self._op_flow),
            phase="transcribing" if self._state == "processing" else None,
        )

    def _start_locked(self, handler_id: str, client_id: str | None) -> None:
        op_seq = self._op_counter + 1
        self._op_counter = op_seq
        self._op_seq = op_seq
        self._op_flow = handler_id
        self._op_client_id = client_id
        self._op_superseded = threading.Event()
        self._active_handler_id = None
        self._state = "recording"
        # Published BEFORE the recorder starts: the mic may report "opened" at once,
        # and mic_live is only accepted for the operation that is recording.
        self._publish_state_locked()
        # The start cue plays once the mic is LIVE (on the recorder's open thread):
        # it never blocks the trigger, and it doesn't race the mic open for the device.
        started = self._recorder.start(
            on_failure=lambda reason: self._on_mic_failure(op_seq, reason),
            on_opened=lambda: self._on_mic_opened(op_seq),
        )
        if not started:
            self._state = "idle"
            if self._status is not None:
                self._status.mark_idle(op_seq)
            return
        print(_("[VoiceMate] 🎙  Recording... (press to stop)"))
        self._schedule_timers_locked(op_seq)

    def _stop_locked(self, handler_id: str) -> None:
        self._cancel_timers()
        self._state = "processing"
        self._op_flow = handler_id  # stop decides the destination
        self._publish_state_locked()
        status = self._status
        op = (
            status.operation(self._op_seq, handler_id, self._op_client_id, self._op_superseded)
            if status is not None
            else OperationHandle(op_seq=self._op_seq, flow=handler_id, superseded=self._op_superseded)
        )
        threading.Thread(target=self._stop_and_dispatch, args=(op, handler_id), daemon=True, name="Transcribe").start()

    def _supersede_locked(self) -> TranscriptionHandler | None:
        """Mark the operation in processing as taken over; returns its busy handler, if any."""
        self._op_superseded.set()
        active = self._active_handler_id
        self._active_handler_id = None
        return self._handlers[active] if active is not None else None

    # -- microphone callbacks (recorder threads) --------------------------------
    def _on_mic_opened(self, op_seq: int) -> None:
        play = self._status.set_mic_live(op_seq) if self._status is not None else True
        if not play:
            return
        try:
            self._audio.recording_started()
        except Exception as exc:  # noqa: BLE001 (best-effort cue; the recording itself is fine)
            print(_("[VoiceMate] ⚠ Could not play the start beep ({exc}).").format(exc=exc), file=sys.stderr)

    def _on_mic_failure(self, op_seq: int, reason: str) -> None:
        """The microphone could not be opened for `op_seq` (called off-thread by the Recorder).

        Returns the session to idle (so the next press STARTS again instead of
        "stopping" a recording that never captured anything) and publishes an error
        event, which the Windows side turns into a notification.
        """
        with self._lock:
            if self._op_seq != op_seq:
                return  # a newer operation owns the mic now and reports its own failures
            client_id = self._op_client_id
            active = self._state == "recording"
            if active:
                self._cancel_timers()
                self._state = "idle"
        if active and self._status is not None:
            self._status.mark_idle(op_seq)
        print(
            _(
                "[VoiceMate] 🎙 ✗ Microphone unavailable ({reason}). Check that a microphone is connected "
                "and enabled, then press the hotkey again."
            ).format(reason=reason),
            file=sys.stderr,
        )
        play = True
        if self._status is not None:
            play = self._status.publish_error(
                "mic_unavailable",
                detail=reason,
                message=_("Microphone unavailable: {reason}").format(reason=reason),
                op_seq=op_seq,
                client_id=client_id,
            )
        if play:
            self._beep_error_async()

    def _beep_error_async(self) -> None:
        """Error cue off the caller's thread; skipped while a previous one is stuck."""
        if self._error_beep_busy.is_set():
            return
        self._error_beep_busy.set()

        def _run() -> None:
            try:
                self._audio.error()
            except Exception:  # noqa: BLE001, S110 (best-effort beep: the audio device may be the problem)
                pass
            finally:
                self._error_beep_busy.clear()

        threading.Thread(target=_run, daemon=True, name="ErrorBeep").start()

    # -- time limit ------------------------------------------------------------
    def _schedule_timers_locked(self, op_seq: int) -> None:
        warning_at = self._max_seconds * self._warning_percent
        self._warning_timer = threading.Timer(warning_at, self._on_warning, args=(op_seq,))
        self._warning_timer.daemon = True
        self._warning_timer.start()
        self._timeout_timer = threading.Timer(float(self._max_seconds), self._on_timeout, args=(op_seq,))
        self._timeout_timer.daemon = True
        self._timeout_timer.start()

    def _cancel_timers(self) -> None:
        if self._warning_timer is not None:
            self._warning_timer.cancel()
            self._warning_timer = None
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

    def _on_warning(self, op_seq: int) -> None:
        with self._lock:
            if self._state != "recording" or self._op_seq != op_seq:
                return
        remaining = self._max_seconds * (1 - self._warning_percent)
        print(_("[VoiceMate] ⚠ Recording will end in {remaining:.0f}s").format(remaining=remaining))
        play = True
        if self._status is not None:
            play = self._status.publish_warning(
                "time_limit_soon",
                detail=f"{remaining:.0f}s left of {self._max_seconds}s",
                message=_("Recording will end in {remaining:.0f}s.").format(remaining=remaining),
                op_seq=op_seq,
            )
        if play:
            self._audio.timeout_warning()

    def _on_timeout(self, op_seq: int) -> None:
        with self._lock:
            if self._state != "recording" or self._op_seq != op_seq:
                return
            print(_("[VoiceMate] ⏰ Maximum time reached. Ending recording..."))
            self._stop_locked(self._default_handler_id)

    # -- processing (its own thread) --------------------------------------------
    def _stop_and_dispatch(self, op: OperationHandle, handler_id: str) -> None:
        """Stop the recording, transcribe and dispatch (runs in its own thread).

        Any exception here is logged (traceback) and the state ALWAYS returns to
        idle: a daemon thread that dies silently would leave the session stuck in
        `processing` and the toggle "dead" with no clue in the log.
        """
        try:
            result: NDArray[np.float32] | None = self._recorder.stop()
            with self._lock:
                if self._state != "processing" or self._op_seq != op.op_seq:
                    # Restarted or cancelled before the audio was read: abort silently.
                    return
                self._active_handler_id = handler_id

            if result is None:
                print(_("[VoiceMate] No audio captured."))
                self._warn(op, "no_audio", "", _("No audio captured."))
                return

            duration = len(result) / self._sample_rate
            print(_("[VoiceMate] ⏳ Transcribing {duration:.1f}s of audio...").format(duration=duration))
            started_at = time.perf_counter()
            text = self._transcriber.transcribe(result)
            self._warn_if_slow(op, duration, time.perf_counter() - started_at)
            if op.cancelled():
                return
            if not text:
                print(_("[VoiceMate] No speech detected."))
                self._warn(op, "no_speech", f"{duration:.1f}s of audio", _("No speech detected."))
                return

            self._handlers[handler_id].handle(text, op)
        except Exception as exc:  # noqa: BLE001 (thread boundary: log + recover)
            print(_("[VoiceMate] ❌ Error processing the recording:"), file=sys.stderr)
            traceback.print_exc()
            play = op.publish_error(
                "transcription_failed",
                detail=_describe(exc),
                message=_("Transcription failed: {detail}").format(detail=_describe(exc)),
            )
            if play:
                try:
                    self._audio.error()
                except Exception:  # noqa: BLE001, S110 (beep is best-effort)
                    pass
        finally:
            self._finish_processing(op.op_seq)

    def _warn(self, op: OperationHandle, code: WarningCode, detail: str, message: str) -> None:
        """Informational warning of `op`, only while it is still the current operation (a stale
        "no speech" must not disturb a recording that already took over)."""
        with self._lock:
            current = self._op_seq == op.op_seq
        if current and op.active() and self._status is not None:
            self._status.publish_warning(code, detail=detail, message=message, op_seq=op.op_seq)

    def _warn_if_slow(self, op: OperationHandle, audio_seconds: float, elapsed: float) -> None:
        """Detect a GPU-less backend (silent fallback to CPU/software).

        Healthy GPU transcription runs well below real time; 3x the audio
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
        self._warn(
            op,
            "slow_backend",
            f"{elapsed:.1f}s for {audio_seconds:.1f}s of audio",
            _("Transcription is much slower than the audio: the backend is probably running without a GPU."),
        )

    def _finish_processing(self, op_seq: int) -> None:
        with self._lock:
            done = self._state == "processing" and self._op_seq == op_seq
            if done:
                self._state = "idle"
                self._active_handler_id = None
        # Publish idle OUTSIDE the session lock (the hub has its own lock) and only
        # if this finalization is the one that actually returned to idle: mark_idle
        # ignores it if a new operation already opened on top (race-free).
        if done and self._status is not None:
            self._status.mark_idle(op_seq)
