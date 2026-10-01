from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from app.core.config import Config
from app.core.recording_session import RecordingSession
from app.core.session_status import OperationHandle, SessionStatus


class FakeRecorder:
    def __init__(self) -> None:
        self._recording = False
        self._lock = threading.Lock()
        self.start_calls = 0
        self.stop_calls = 0
        self.next_audio: NDArray[np.float32] | None = np.zeros(16000, dtype=np.float32)
        self.on_failure: Callable[[str], None] | None = None
        self.on_opened: Callable[[], None] | None = None
        self.auto_open = True

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(
        self,
        on_failure: Callable[[str], None] | None = None,
        on_opened: Callable[[], None] | None = None,
    ) -> bool:
        with self._lock:
            if self._recording:
                return False
            self._recording = True
            self.start_calls += 1
            self.on_failure = on_failure
            self.on_opened = on_opened
        if self.auto_open and on_opened is not None:
            on_opened()  # a healthy mic opens right away
        return True

    def fail_open(self, reason: str) -> None:
        """Simulate the off-thread mic open failing (what Recorder does on a dead mic)."""
        with self._lock:
            self._recording = False
            callback = self.on_failure
        assert callback is not None
        callback(reason)

    def stop(self) -> NDArray[np.float32] | None:
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            self.stop_calls += 1
        return self.next_audio


class FakeTranscriber:
    def __init__(self, text: str = "texto transcrito") -> None:
        self.text = text
        self.calls: list[NDArray[np.float32]] = []

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        self.calls.append(audio)
        return self.text


class FakeAudio:
    def __init__(self) -> None:
        self.recording_started_calls = 0
        self.transcription_complete_calls = 0
        self.timeout_warning_calls = 0
        self.error_calls = 0

    def recording_started(self) -> None:
        self.recording_started_calls += 1

    def transcription_complete(self) -> None:
        self.transcription_complete_calls += 1

    def timeout_warning(self) -> None:
        self.timeout_warning_calls += 1

    def error(self) -> None:
        self.error_calls += 1


class FakeHandler:
    def __init__(self, block_event: threading.Event | None = None) -> None:
        self.handle_calls: list[str] = []
        self.ops: list[OperationHandle | None] = []
        self.cancel_calls = 0
        self.close_calls = 0
        self.block_event = block_event
        self.handle_started = threading.Event()
        self.handle_done = threading.Event()
        self._busy = False
        self._lock = threading.Lock()

    def handle(self, text: str, op: OperationHandle | None = None) -> None:
        with self._lock:
            self._busy = True
        self.handle_calls.append(text)
        self.ops.append(op)
        self.handle_started.set()
        if self.block_event is not None:
            self.block_event.wait(timeout=2.0)
        with self._lock:
            self._busy = False
        self.handle_done.set()

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def cancel_in_flight(self) -> None:
        self.cancel_calls += 1
        if self.block_event is not None:
            self.block_event.set()

    def close(self) -> None:
        self.close_calls += 1


def _make_session(
    handlers: dict[str, FakeHandler] | None = None,
    transcriber: FakeTranscriber | None = None,
    status: SessionStatus | None = None,
    config: Config | None = None,
) -> tuple[RecordingSession, FakeRecorder, FakeTranscriber, FakeAudio, dict[str, FakeHandler]]:
    recorder = FakeRecorder()
    transcriber = transcriber or FakeTranscriber()
    audio = FakeAudio()
    handlers = handlers or {"clipboard": FakeHandler()}
    session = RecordingSession(
        recorder=recorder,  # type: ignore[arg-type]
        transcriber=transcriber,  # type: ignore[arg-type]
        audio=audio,  # type: ignore[arg-type]
        config=config or Config(max_recording_seconds=600),
        handlers=handlers,  # type: ignore[arg-type]
        default_handler_id=next(iter(handlers)),
        status=status,
        flow_kinds={"clipboard": "clipboard", "claude_chat": "claude_chat"},
    )
    return session, recorder, transcriber, audio, handlers


def test_toggle_idle_starts_recording() -> None:
    session, recorder, _, audio, _ = _make_session()

    session.toggle("clipboard")

    assert recorder.start_calls == 1
    assert audio.recording_started_calls == 1
    assert recorder.is_recording


def test_toggle_recording_dispatches_to_handler_of_stop() -> None:
    handlers = {"clipboard": FakeHandler(), "claude_chat": FakeHandler()}
    session, recorder, _, _, _ = _make_session(handlers=handlers)

    session.toggle("clipboard")  # start
    session.toggle("claude_chat")  # stop with a different handler
    assert handlers["claude_chat"].handle_started.wait(timeout=2.0)
    assert handlers["claude_chat"].handle_done.wait(timeout=2.0)

    assert handlers["clipboard"].handle_calls == []
    assert handlers["claude_chat"].handle_calls == ["texto transcrito"]
    assert recorder.stop_calls == 1


def test_toggle_unknown_handler_id_is_noop() -> None:
    session, recorder, _, _, _ = _make_session()
    session.toggle("nope")
    assert recorder.start_calls == 0


def test_no_audio_skips_handler_call() -> None:
    handler = FakeHandler()
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler})
    recorder.next_audio = None

    session.toggle("clipboard")
    session.toggle("clipboard")
    # Wait for the stop thread to complete
    deadline = time.monotonic() + 2.0
    while recorder.stop_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)

    assert handler.handle_calls == []


def test_no_transcription_text_skips_handler_call() -> None:
    handler = FakeHandler()
    transcriber = FakeTranscriber(text="")
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler}, transcriber=transcriber)

    session.toggle("clipboard")
    session.toggle("clipboard")
    deadline = time.monotonic() + 2.0
    while recorder.stop_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)

    assert handler.handle_calls == []


def test_toggle_during_processing_cancels_and_restarts() -> None:
    block = threading.Event()
    handler = FakeHandler(block_event=block)
    session, recorder, _, _, _ = _make_session(handlers={"claude_chat": handler})

    session.toggle("claude_chat")  # start
    session.toggle("claude_chat")  # stop → enters processing → handler.handle blocks on block
    assert handler.handle_started.wait(timeout=2.0)
    assert handler.is_busy()

    # third toggle: cancels the pending AI call and starts a new recording
    session.toggle("claude_chat")

    # cancel_in_flight was called and handler.handle returned
    assert handler.cancel_calls == 1
    assert handler.handle_done.wait(timeout=2.0)
    # recorder started a new recording
    assert recorder.start_calls == 2
    assert recorder.is_recording


def test_transcriber_exception_recovers_to_idle() -> None:
    """An exception during transcription must not leave the session stuck in `processing`:
    the state returns to idle, audio.error() plays, and the next toggle records normally."""

    class BoomTranscriber(FakeTranscriber):
        def transcribe(self, audio: NDArray[np.float32]) -> str:
            raise RuntimeError("whisper explodiu")

    handler = FakeHandler()
    session, recorder, _, audio, _ = _make_session(handlers={"clipboard": handler}, transcriber=BoomTranscriber())

    session.toggle("clipboard")  # start
    session.toggle("clipboard")  # stop → transcribe raises on the thread
    deadline = time.monotonic() + 2.0
    while audio.error_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert audio.error_calls == 1
    assert handler.handle_calls == []

    # Session recovered: a new toggle starts recording again (idle state).
    session.toggle("clipboard")
    assert recorder.start_calls == 2
    assert recorder.is_recording


def test_slow_transcription_warns_once(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Slow backend (no GPU) → RTF warning printed once per session."""
    from app.core import recording_session as rs

    # perf_counter returns 0 before and 30 after each transcribe → 30s for 1s of audio.
    ticks = iter([0.0, 30.0] * 10)
    monkeypatch.setattr(rs.time, "perf_counter", lambda: next(ticks))

    handler = FakeHandler()
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler})
    recorder.next_audio = np.zeros(16000, dtype=np.float32)  # 1s @ 16kHz

    session.toggle("clipboard")
    session.toggle("clipboard")
    assert handler.handle_done.wait(timeout=2.0)

    captured = capsys.readouterr()
    assert "slower than the audio" in captured.err
    assert captured.err.count("slower than the audio") == 1


def test_fast_transcription_no_warning(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import recording_session as rs

    ticks = iter([0.0, 0.3] * 10)  # 0.3s for 1s of audio → healthy
    monkeypatch.setattr(rs.time, "perf_counter", lambda: next(ticks))

    handler = FakeHandler()
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler})
    recorder.next_audio = np.zeros(16000, dtype=np.float32)

    session.toggle("clipboard")
    session.toggle("clipboard")
    assert handler.handle_done.wait(timeout=2.0)

    captured = capsys.readouterr()
    assert "mais lenta" not in captured.err


def test_toggle_returns_action_and_op_seq() -> None:
    """toggle returns the operation immediately — the basis for trigger feedback."""
    handlers = {"clipboard": FakeHandler(), "claude_chat": FakeHandler()}
    session, _, _, _, _ = _make_session(handlers=handlers)

    started = session.toggle("clipboard")
    assert started is not None
    assert started.action == "started"
    assert started.op_seq == 1
    assert started.state == "recording"
    assert started.flow == "clipboard"

    stopped = session.toggle("claude_chat")  # stop decides the destination
    assert stopped is not None
    assert stopped.action == "stopped"
    assert stopped.op_seq == 1  # same operation (goes to processing)
    assert stopped.state == "processing"
    assert handlers["claude_chat"].handle_done.wait(timeout=2.0)


def test_toggle_unknown_handler_returns_none() -> None:
    session, _, _, _, _ = _make_session()
    assert session.toggle("nope") is None


def test_status_publishes_recording_then_idle() -> None:
    status = SessionStatus()
    cid = status.register()
    handler = FakeHandler()
    session, _, _, _, _ = _make_session(handlers={"clipboard": handler}, status=status)

    session.toggle("clipboard", client_id=cid)  # start
    st = status.status(cid, "all")
    assert st["state"] == "recording"
    assert st["op_seq"] == 1
    assert st["client_id"] == cid

    session.toggle("clipboard", client_id=cid)  # stop → processing → ... → idle
    assert handler.handle_done.wait(timeout=2.0)
    deadline = time.monotonic() + 2.0
    while status.status(cid, "all")["state"] != "idle" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert status.status(cid, "all")["state"] == "idle"


def test_restart_during_processing_opens_new_op_seq() -> None:
    block = threading.Event()
    handler = FakeHandler(block_event=block)
    status = SessionStatus()
    session, _, _, _, _ = _make_session(handlers={"claude_chat": handler}, status=status)

    session.toggle("claude_chat")  # start (op 1)
    session.toggle("claude_chat")  # stop → processing
    assert handler.handle_started.wait(timeout=2.0)
    restarted = session.toggle("claude_chat")  # cancel + new recording
    assert restarted is not None
    assert restarted.action == "restarted"
    assert restarted.op_seq == 2  # new operation
    assert handler.handle_done.wait(timeout=2.0)


def test_handler_close_not_called_by_session() -> None:
    """RecordingSession does not close handlers — closing is the responsibility of main."""
    handler = FakeHandler()
    session, _, _, _, _ = _make_session(handlers={"clipboard": handler})
    session.toggle("clipboard")
    session.toggle("clipboard")
    assert handler.handle_done.wait(timeout=2.0)
    assert handler.close_calls == 0


def _wait_for(pred: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


def test_start_cue_plays_only_once_the_mic_is_live() -> None:
    """The start beep means "the mic is capturing": it waits for the open, and it
    never plays when the open fails."""
    session, recorder, _, audio, _ = _make_session()
    recorder.auto_open = False

    session.toggle("clipboard")
    assert audio.recording_started_calls == 0  # open still in flight

    assert recorder.on_opened is not None
    recorder.on_opened()
    assert audio.recording_started_calls == 1


def test_mic_failure_returns_to_idle_and_publishes_error() -> None:
    """No microphone: the session must not stay "recording" (the next press would STOP
    a recording that never captured anything). It goes back to idle, beeps the error
    and publishes an error event for the Windows side to notify the user."""
    status = SessionStatus()
    session, recorder, _, audio, _ = _make_session(status=status)

    outcome = session.toggle("clipboard", client_id="c1")
    assert outcome is not None and outcome.action == "started"

    recorder.fail_open("PulseAudio: Unable to create stream: Timeout")

    assert status.status(None, "all")["state"] == "idle"
    assert _wait_for(lambda: audio.error_calls == 1)
    res = status.result(None, "all")
    assert res["error"] == "mic_unavailable"
    assert res["client_id"] == "c1"
    assert res["text"] == ""
    assert "Unable to create stream" in str(res["message"])

    # The next press starts a NEW recording (not a stop).
    again = session.toggle("clipboard", client_id="c1")
    assert again is not None and again.action == "started"
    assert again.op_seq == outcome.op_seq + 1


def test_late_mic_failure_of_an_old_operation_does_not_touch_the_new_one() -> None:
    status = SessionStatus()
    session, recorder, _, _, _ = _make_session(status=status)

    session.toggle("clipboard")
    stale_failure = recorder.on_failure
    session.toggle("clipboard")  # stop → processing → idle
    deadline = time.monotonic() + 2.0
    while status.status(None, "all")["state"] != "idle" and time.monotonic() < deadline:
        time.sleep(0.01)
    started = session.toggle("clipboard")  # new operation, recording
    assert started is not None and started.action == "started"

    assert stale_failure is not None
    stale_failure("late failure from the previous open")

    assert status.status(None, "all")["state"] == "recording"  # untouched
    assert status.result(None, "all")["error"] is None  # stale failure is not reported


def test_mic_failure_after_the_user_already_pressed_stop() -> None:
    """The open fails only after the user pressed stop (state processing): the flow
    still ends idle and the failure is reported exactly once."""
    status = SessionStatus()
    session, recorder, _, audio, _ = _make_session(status=status)
    recorder.auto_open = False
    recorder.next_audio = None  # nothing was captured

    session.toggle("clipboard")
    failure = recorder.on_failure
    session.toggle("clipboard")  # stop → processing
    assert failure is not None
    failure("timed out opening the microphone after 6s")

    assert _wait_for(lambda: status.status(None, "all")["state"] == "idle")
    assert _wait_for(lambda: audio.error_calls == 1)
    assert status.result(None, "all")["error"] == "mic_unavailable"
    assert status.result(None, "all", since=1)["seq"] == 1  # one event only


def test_error_beep_is_single_flight() -> None:
    """With a dead audio server the error beep hangs; repeated failures must not
    pile up stuck beep threads."""
    release = threading.Event()
    session, recorder, _, audio, _ = _make_session()

    def _stuck_error() -> None:
        audio.error_calls += 1
        release.wait(timeout=5.0)

    audio.error = _stuck_error  # type: ignore[method-assign]
    for _attempt in range(3):
        session.toggle("clipboard")
        recorder.fail_open("no device")
    assert _wait_for(lambda: audio.error_calls == 1)
    time.sleep(0.05)
    assert audio.error_calls == 1  # the 2nd and 3rd were skipped while the 1st is stuck
    release.set()


# -- API v2: expect, cancel, events, cue lease -------------------------------------------


def _client(status: SessionStatus, *caps: str) -> tuple[str, int]:
    reg = status.hub.register(name="test", capabilities=caps)  # type: ignore[arg-type]
    return reg["client_id"], reg["cursor"]


def _events(status: SessionStatus, client_id: str, since: int) -> list[dict[str, Any]]:
    """Events after `since`, flattened: {"type": ..., **data}."""
    response = status.hub.poll(client_id, instance=status.instance, since=since, wait=0)
    return [{"type": event["type"], **event["data"]} for event in response["events"]]


def _wait_idle(status: SessionStatus) -> bool:
    return _wait_for(lambda: status.hub.state()["state"] == "idle")


def test_expect_makes_triggers_idempotent() -> None:
    block = threading.Event()
    handler = FakeHandler(block_event=block)
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler})

    noop = session.toggle("clipboard", expect="stop")
    assert noop is not None and (noop.action, noop.state) == ("noop", "idle")
    assert recorder.start_calls == 0

    started = session.toggle("clipboard", expect="start")
    assert started is not None and started.action == "started"
    again = session.toggle("clipboard", expect="start")
    assert again is not None and (again.action, again.state, again.op_seq) == ("noop", "recording", started.op_seq)

    stopped = session.toggle("clipboard", expect="stop")
    assert stopped is not None and stopped.action == "stopped"
    assert handler.handle_started.wait(timeout=2.0)
    late_stop = session.toggle("clipboard", expect="stop")
    assert late_stop is not None and (late_stop.action, late_stop.state) == ("noop", "processing")

    restarted = session.toggle("clipboard", expect="start")
    assert restarted is not None and (restarted.action, restarted.op_seq) == ("restarted", started.op_seq + 1)
    assert handler.cancel_calls == 1
    block.set()


def test_restart_supersedes_the_operation_in_processing() -> None:
    block = threading.Event()
    handler = FakeHandler(block_event=block)
    session, _, _, _, _ = _make_session(handlers={"claude_chat": handler}, status=SessionStatus())

    session.toggle("claude_chat")
    session.toggle("claude_chat")
    assert handler.handle_started.wait(timeout=2.0)
    old = handler.ops[0]
    assert old is not None and old.active()
    session.toggle("claude_chat")  # restart
    assert not old.active()  # the handler must not start new work for it
    assert not old.cancelled()  # ... but what it already produced may still be published
    block.set()


def test_state_events_follow_the_flow_semantics_and_mic_live() -> None:
    status = SessionStatus()
    cid, cursor = _client(status)
    handlers = {"clipboard": FakeHandler(), "claude_chat": FakeHandler()}
    session, recorder, _, _, _ = _make_session(handlers=handlers, status=status)
    recorder.auto_open = False

    session.toggle("clipboard", client_id="c9")
    assert recorder.on_opened is not None
    recorder.on_opened()  # the mic is live
    session.toggle("claude_chat")  # stop decides the destination
    assert handlers["claude_chat"].handle_done.wait(timeout=2.0)
    assert _wait_idle(status)

    states = [e for e in _events(status, cid, cursor) if e["type"] == "state"]
    summary = [(s["state"], s["phase"], s["flow"], s["flow_kind"], s["mic_live"]) for s in states]
    assert summary == [
        ("recording", None, "clipboard", "clipboard", False),
        ("recording", None, "clipboard", "clipboard", True),
        ("processing", "transcribing", "claude_chat", "claude_chat", False),
        ("idle", None, None, None, False),
    ]
    assert all(s["client_id"] == "c9" for s in states)


def test_cue_lease_silences_the_daemon_beeps() -> None:
    status = SessionStatus()
    _client(status, "cues")
    session, recorder, _, audio, _ = _make_session(status=status)

    session.toggle("clipboard")
    assert audio.recording_started_calls == 0  # the client plays the start cue
    recorder.fail_open("no device")
    time.sleep(0.05)
    assert audio.error_calls == 0  # ... and the error cue
    assert status.result(None, "all")["error"] == "mic_unavailable"


def test_cancel_while_recording_discards_the_audio() -> None:
    status = SessionStatus()
    handler = FakeHandler()
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler}, status=status)

    started = session.toggle("clipboard")
    assert started is not None
    assert session.cancel(op_seq=started.op_seq + 1).action == "noop"  # not the current operation
    outcome = session.cancel(op_seq=started.op_seq)
    assert (outcome.action, outcome.state) == ("cancelled", "idle")
    assert recorder.stop_calls == 1
    assert not recorder.is_recording
    assert status.hub.state()["state"] == "idle"
    assert session.cancel().action == "noop"  # nothing left
    time.sleep(0.05)
    assert handler.handle_calls == []


def test_cancel_while_processing_publishes_no_result() -> None:
    gate = threading.Event()

    class SlowTranscriber(FakeTranscriber):
        def transcribe(self, audio: NDArray[np.float32]) -> str:
            gate.wait(timeout=5.0)
            return "too late"

    status = SessionStatus()
    handler = FakeHandler()
    session, _, _, _, _ = _make_session(handlers={"clipboard": handler}, transcriber=SlowTranscriber(), status=status)
    started = session.toggle("clipboard")
    session.toggle("clipboard")  # processing, transcription held
    assert started is not None
    assert session.cancel().action == "cancelled"
    gate.set()
    time.sleep(0.1)
    assert handler.handle_calls == []
    assert status.is_cancelled(started.op_seq)
    nxt = session.toggle("clipboard")  # a fresh operation works normally
    assert nxt is not None and (nxt.action, nxt.op_seq) == ("started", started.op_seq + 1)


@pytest.mark.parametrize(("audio_data", "text", "code"), [(None, "x", "no_audio"), (np.zeros(16000), "", "no_speech")])
def test_empty_recordings_publish_a_warning(audio_data: NDArray[np.float32] | None, text: str, code: str) -> None:
    status = SessionStatus()
    cid, cursor = _client(status)
    session, recorder, _, audio, _ = _make_session(transcriber=FakeTranscriber(text=text), status=status)
    recorder.next_audio = audio_data
    session.toggle("clipboard")
    session.toggle("clipboard")
    assert _wait_idle(status)
    warnings = [e for e in _events(status, cid, cursor) if e["type"] == "warning"]
    assert [w["code"] for w in warnings] == [code]
    assert warnings[0]["message"]
    assert audio.error_calls == 0


def test_time_limit_warning_is_an_event_with_a_cue() -> None:
    status = SessionStatus()
    cid, cursor = _client(status)
    config = Config(max_recording_seconds=30, timeout_warning_percent=0.002)  # warns after 60 ms
    session, _, _, audio, _ = _make_session(status=status, config=config)
    session.toggle("clipboard")
    assert _wait_for(lambda: audio.timeout_warning_calls == 1)
    warnings = [e for e in _events(status, cid, cursor) if e["type"] == "warning"]
    assert [w["code"] for w in warnings] == ["time_limit_soon"]
    assert warnings[0]["needs_cue"] is False  # nobody holds `cues`: the daemon beeped
    session.toggle("clipboard")  # stop before the 30 s limit
    assert _wait_idle(status)


def test_slow_backend_warning_is_published_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import recording_session as rs

    ticks = iter([0.0, 30.0] * 10)
    monkeypatch.setattr(rs.time, "perf_counter", lambda: next(ticks))
    status = SessionStatus()
    cid, cursor = _client(status)
    handler = FakeHandler()
    session, _, _, _, _ = _make_session(handlers={"clipboard": handler}, status=status)
    for _attempt in range(2):
        handler.handle_done.clear()
        session.toggle("clipboard")
        session.toggle("clipboard")
        assert handler.handle_done.wait(timeout=2.0)
        assert _wait_idle(status)
    codes = [e["code"] for e in _events(status, cid, cursor) if e["type"] == "warning"]
    assert codes == ["slow_backend"]


def test_transcription_failure_is_an_error_event() -> None:
    class BoomTranscriber(FakeTranscriber):
        def transcribe(self, audio: NDArray[np.float32]) -> str:
            raise RuntimeError("whisper exploded")

    status = SessionStatus()
    cid, cursor = _client(status)
    session, _, _, audio, _ = _make_session(transcriber=BoomTranscriber(), status=status)
    session.toggle("clipboard")
    session.toggle("clipboard")
    assert _wait_for(lambda: audio.error_calls == 1)
    assert _wait_idle(status)
    errors = [e for e in _events(status, cid, cursor) if e["type"] == "error"]
    assert [(e["code"], e["detail"]) for e in errors] == [("transcription_failed", "whisper exploded")]
    assert status.result(None, "all")["error"] == "transcription_failed"
