from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from app.core.session_status import OperationHandle, SessionStatus
from app.features.claude.chat_handler import ClaudeChatHandler


class FakeRuntime:
    def __init__(
        self,
        response: str = "resposta da IA",
        block_event: threading.Event | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.response = response
        self.block_event = block_event
        self.raise_exc = raise_exc
        self.send_calls: list[str] = []
        self.interrupt_calls = 0
        self.stop_calls = 0

    def send_and_collect(self, prompt: str, timeout: float | None = None) -> str:
        self.send_calls.append(prompt)
        if self.block_event is not None:
            self.block_event.wait(timeout=2.0)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response

    def stream(self, prompt: str, timeout: float | None = None) -> Iterator[str]:
        self.send_calls.append(prompt)
        if self.block_event is not None:
            self.block_event.wait(timeout=2.0)
        if self.raise_exc is not None:
            raise self.raise_exc
        yield self.response

    def interrupt(self) -> None:
        self.interrupt_calls += 1
        if self.block_event is not None:
            self.block_event.set()

    def stop(self) -> None:
        self.stop_calls += 1


class FakeAudio:
    def __init__(self) -> None:
        self.ai_response_ready_calls = 0
        self.error_calls = 0

    def transcription_complete(self) -> None:
        pass

    def ai_response_ready(self) -> None:
        self.ai_response_ready_calls += 1

    def error(self) -> None:
        self.error_calls += 1


class FakeSpeaker:
    def __init__(self, active: bool = False) -> None:
        self._active = active
        self.speak_calls: list[str] = []
        self.stop_calls = 0
        self.close_calls = 0
        self.wait_done_calls = 0

    def is_active(self) -> bool:
        return self._active

    def speak(self, text: str) -> None:
        self.speak_calls.append(text)

    def warmup(self) -> None:
        pass

    def wait_done(self, timeout: float | None = None) -> bool:
        self.wait_done_calls += 1
        return True

    def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class FakeClipboard:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def copy(self, text: str) -> None:
        self.copied.append(text)


def _handler(
    runtime: FakeRuntime,
    audio: FakeAudio,
    speaker: FakeSpeaker,
    clipboard: FakeClipboard | None = None,
    timeout_seconds: float = 120.0,
) -> ClaudeChatHandler:
    cb = clipboard or FakeClipboard()
    return ClaudeChatHandler(runtime, audio, speaker, timeout_seconds=timeout_seconds, clipboard=cb)  # type: ignore[arg-type]


def test_handle_copies_transcription_then_response() -> None:
    clipboard = FakeClipboard()
    runtime = FakeRuntime(response="resposta")
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker, clipboard)

    handler.handle("pergunta")

    assert runtime.send_calls == ["pergunta"]
    assert clipboard.copied == ["pergunta", "resposta"]
    assert audio.ai_response_ready_calls == 1
    assert speaker.speak_calls == []
    assert audio.error_calls == 0
    assert handler.is_busy() is False


def test_handle_with_active_speaker_uses_tts_instead_of_beep() -> None:
    clipboard = FakeClipboard()
    runtime = FakeRuntime(response="resposta falada")
    audio = FakeAudio()
    speaker = FakeSpeaker(active=True)
    handler = _handler(runtime, audio, speaker, clipboard)

    handler.handle("pergunta")

    assert clipboard.copied == ["pergunta", "resposta falada"]
    assert speaker.speak_calls == ["resposta falada"]
    assert audio.ai_response_ready_calls == 0


def test_streaming_announces_first_token(capsys: pytest.CaptureFixture[str]) -> None:
    """On the 1st stream delta, announces that Claude started responding."""
    runtime = FakeRuntime(response="resposta falada")
    speaker = FakeSpeaker(active=True)
    handler = _handler(runtime, FakeAudio(), speaker)

    handler.handle("pergunta")

    out = capsys.readouterr().out
    assert "Claude responding..." in out


def test_heartbeat_prints_while_processing(capsys: pytest.CaptureFixture[str]) -> None:
    """The heartbeat (non-TTS path) prints 'processando' while Claude thinks."""
    from app.features.claude.chat_handler import _Heartbeat

    with _Heartbeat(interval=0.05):
        time.sleep(0.16)
    out = capsys.readouterr().out
    assert "Claude processing..." in out


def test_heartbeat_silent_when_fast(capsys: pytest.CaptureFixture[str]) -> None:
    from app.features.claude.chat_handler import _Heartbeat

    with _Heartbeat(interval=10.0):
        pass  # exits before the 1st tick
    assert "processing" not in capsys.readouterr().out


def test_handle_copies_transcription_before_calling_runtime() -> None:
    events: list[str] = []

    class OrderingClipboard:
        def copy(self, text: str) -> None:
            events.append(f"copy:{text}")

    class OrderingRuntime(FakeRuntime):
        def send_and_collect(self, prompt: str, timeout: float | None = None) -> str:
            events.append(f"send:{prompt}")
            return "resposta"

    runtime = OrderingRuntime()
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = ClaudeChatHandler(runtime, audio, speaker, clipboard=OrderingClipboard())  # type: ignore[arg-type]

    handler.handle("pergunta")

    assert events == ["copy:pergunta", "send:pergunta", "copy:resposta"]


def test_handle_exception_keeps_transcription_in_clipboard() -> None:
    clipboard = FakeClipboard()
    runtime = FakeRuntime(raise_exc=RuntimeError("boom"))
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker, clipboard)

    handler.handle("pergunta")

    assert clipboard.copied == ["pergunta"]
    assert audio.error_calls == 1
    assert audio.ai_response_ready_calls == 0
    assert handler.is_busy() is False


def test_cancel_in_flight_keeps_transcription_discards_response() -> None:
    clipboard = FakeClipboard()
    block_event = threading.Event()
    runtime = FakeRuntime(response="resposta tardia", block_event=block_event)
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker, clipboard)

    worker = threading.Thread(target=handler.handle, args=("pergunta",), daemon=True)
    worker.start()
    deadline = time.monotonic() + 1.0
    while not handler.is_busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert handler.is_busy() is True

    handler.cancel_in_flight()
    worker.join(timeout=2.0)

    assert runtime.interrupt_calls == 1
    assert speaker.stop_calls == 1
    assert clipboard.copied == ["pergunta"]
    assert audio.ai_response_ready_calls == 0


def test_cancel_in_flight_noop_when_idle() -> None:
    runtime = FakeRuntime()
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker)

    handler.cancel_in_flight()

    assert runtime.interrupt_calls == 0
    assert speaker.stop_calls == 0


def test_close_stops_runtime_and_speaker() -> None:
    runtime = FakeRuntime()
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker)

    handler.close()

    assert runtime.stop_calls == 1
    assert speaker.close_calls == 1


def test_handle_timeout_does_not_hang_and_marks_idle() -> None:
    import concurrent.futures

    clipboard = FakeClipboard()

    class TimeoutRuntime(FakeRuntime):
        def send_and_collect(self, prompt: str, timeout: float | None = None) -> str:
            raise concurrent.futures.TimeoutError

    runtime = TimeoutRuntime()
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker, clipboard, timeout_seconds=0.5)

    handler.handle("pergunta")

    assert clipboard.copied == ["pergunta"]  # transcription preserved
    assert audio.error_calls == 1
    assert audio.ai_response_ready_calls == 0
    assert runtime.interrupt_calls == 1  # tried to interrupt the stuck runtime
    assert handler.is_busy() is False  # returned to idle


def test_handle_passes_configured_timeout_to_runtime() -> None:
    captured: dict[str, float | None] = {}

    class TimeoutCapturingRuntime(FakeRuntime):
        def send_and_collect(self, prompt: str, timeout: float | None = None) -> str:
            captured["timeout"] = timeout
            return "ok"

    runtime = TimeoutCapturingRuntime()
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker, timeout_seconds=42.0)

    handler.handle("oi")

    assert captured["timeout"] == 42.0


def _processing(status: SessionStatus, op_seq: int = 1) -> OperationHandle:
    status.set_operation(op_seq, "processing", "claude_chat", "c1", flow_kind="claude_chat", phase="transcribing")
    return status.operation(op_seq, "claude_chat", "c1")


def _events(status: SessionStatus, client_id: str, since: int) -> list[dict[str, object]]:
    response = status.hub.poll(client_id, instance=status.instance, since=since, wait=0)
    return [{"type": e["type"], **e["data"]} for e in response["events"]]


def test_phases_and_results_with_tts() -> None:
    """thinking while Claude works, speaking from the first sentence handed to the TTS;
    the transcript is an intermediate result, the spoken answer the final one."""
    status = SessionStatus()
    reg = status.hub.register(name="companion")
    op = _processing(status)
    handler = _handler(FakeRuntime(response="resposta falada"), FakeAudio(), FakeSpeaker(active=True))

    handler.handle("pergunta", op)

    events = _events(status, reg["client_id"], reg["cursor"])
    phases = [e["phase"] for e in events if e["type"] == "state"]
    assert phases == ["transcribing", "thinking", "speaking"]
    results = [(e["kind"], e["text"], e["final"], e["spoken"]) for e in events if e["type"] == "result"]
    assert results == [("transcript", "pergunta", False, False), ("ai_response", "resposta falada", True, True)]


def test_phases_without_tts_skip_speaking() -> None:
    status = SessionStatus()
    op = _processing(status)
    reg = status.hub.register(name="companion")
    audio = FakeAudio()
    handler = _handler(FakeRuntime(response="resposta"), audio, FakeSpeaker(active=False))

    handler.handle("pergunta", op)

    events = _events(status, reg["client_id"], reg["cursor"])
    assert [e["phase"] for e in events if e["type"] == "state"] == ["thinking"]
    assert [(e["final"], e["spoken"]) for e in events if e["type"] == "result"] == [(False, False), (True, False)]
    assert audio.ai_response_ready_calls == 1


def test_leases_move_clipboard_and_ai_ready_cue_to_the_client() -> None:
    status = SessionStatus()
    status.hub.register(name="companion", capabilities=["clipboard", "cues"])
    clipboard = FakeClipboard()
    audio = FakeAudio()
    handler = _handler(FakeRuntime(response="resposta"), audio, FakeSpeaker(active=False), clipboard)

    handler.handle("pergunta", _processing(status))

    assert clipboard.copied == []
    assert audio.ai_response_ready_calls == 0
    pending = status.hub.results("unacked", 10)["results"]
    assert [(r["kind"], r["delivery"]) for r in pending] == [("transcript", "pending"), ("ai_response", "pending")]


def test_chat_failure_is_an_error_event() -> None:
    status = SessionStatus()
    op = _processing(status)
    reg = status.hub.register(name="companion")
    audio = FakeAudio()
    handler = _handler(FakeRuntime(raise_exc=RuntimeError("rate limited")), audio, FakeSpeaker(active=False))

    handler.handle("pergunta", op)

    errors = [e for e in _events(status, reg["client_id"], reg["cursor"]) if e["type"] == "error"]
    assert [(e["code"], e["detail"]) for e in errors] == [("chat_failed", "rate limited")]
    assert audio.error_calls == 1
    assert status.result(None, "all")["error"] == "chat_failed"  # also in the v1 stream


def test_superseded_operation_keeps_the_transcript_but_does_not_call_claude() -> None:
    """A restart took over between the transcription and the handler: the user's words are
    still delivered (as the operation's final result), Claude is not called."""
    status = SessionStatus()
    superseded = threading.Event()
    superseded.set()
    op = status.operation(1, "claude_chat", None, superseded)
    runtime = FakeRuntime()
    clipboard = FakeClipboard()
    handler = _handler(runtime, FakeAudio(), FakeSpeaker(active=False), clipboard)

    handler.handle("pergunta", op)

    assert runtime.send_calls == []
    assert clipboard.copied == ["pergunta"]
    (record,) = status.hub.results("recent", 10)["results"]
    assert (record["kind"], record["final"]) == ("transcript", True)


def test_an_answer_the_tts_could_not_finish_is_not_marked_spoken() -> None:
    """The TTS died while speaking: the answer gets the ai_ready cue and spoken=false."""

    class DyingSpeaker(FakeSpeaker):
        def wait_done(self, timeout: float | None = None) -> bool:
            self._active = False  # e.g. the audio device vanished mid-answer
            return False

    status = SessionStatus()
    reg = status.hub.register(name="companion")
    audio = FakeAudio()
    handler = _handler(FakeRuntime(response="resposta"), audio, DyingSpeaker(active=True))

    handler.handle("pergunta", _processing(status))

    answers = [e for e in _events(status, reg["client_id"], reg["cursor"]) if e.get("kind") == "ai_response"]
    assert [(a["final"], a["spoken"]) for a in answers] == [(True, False)]
    assert audio.ai_response_ready_calls == 1
