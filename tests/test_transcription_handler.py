from __future__ import annotations

from app.core.session_status import OperationHandle, SessionStatus
from app.core.transcription_handler import ClipboardHandler


class FakeClipboard:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def copy(self, text: str) -> None:
        self.copied.append(text)


class FakeAudio:
    def __init__(self) -> None:
        self.transcription_complete_calls = 0
        self.ai_response_ready_calls = 0
        self.error_calls = 0

    def transcription_complete(self) -> None:
        self.transcription_complete_calls += 1

    def ai_response_ready(self) -> None:
        self.ai_response_ready_calls += 1

    def error(self) -> None:
        self.error_calls += 1


def test_clipboard_handler_copies_and_beeps() -> None:
    clipboard = FakeClipboard()
    audio = FakeAudio()
    handler = ClipboardHandler(audio, clipboard=clipboard)  # type: ignore[arg-type]

    handler.handle("texto transcrito")

    assert clipboard.copied == ["texto transcrito"]
    assert audio.transcription_complete_calls == 1


def test_clipboard_handler_is_not_busy_and_cancel_is_noop() -> None:
    audio = FakeAudio()
    handler = ClipboardHandler(audio)  # type: ignore[arg-type]

    assert handler.is_busy() is False
    handler.cancel_in_flight()  # must not raise
    handler.close()  # must not raise


def _op(status: SessionStatus, op_seq: int = 1) -> OperationHandle:
    return status.operation(op_seq, "clipboard", "c1")


def test_publishes_the_final_transcript_and_writes_the_clipboard_without_a_holder() -> None:
    status = SessionStatus()
    clipboard = FakeClipboard()
    audio = FakeAudio()
    ClipboardHandler(audio, clipboard=clipboard).handle("olá", _op(status))  # type: ignore[arg-type]

    assert clipboard.copied == ["olá"]
    assert audio.transcription_complete_calls == 1
    assert status.result(None, "all")["text"] == "olá"  # the v1 stream (Windows script)
    (record,) = status.hub.results("recent", 10)["results"]
    assert (record["kind"], record["final"], record["delivery"], record["op_seq"]) == ("transcript", True, "daemon", 1)


def test_clipboard_and_cue_leases_leave_both_jobs_to_the_client() -> None:
    status = SessionStatus()
    status.hub.register(name="companion", capabilities=["clipboard", "cues"])
    clipboard = FakeClipboard()
    audio = FakeAudio()
    ClipboardHandler(audio, clipboard=clipboard).handle("olá", _op(status))  # type: ignore[arg-type]

    assert clipboard.copied == []
    assert audio.transcription_complete_calls == 0
    (record,) = status.hub.results("unacked", 10)["results"]
    assert record["delivery"] == "pending"


def test_cancelled_operation_publishes_and_copies_nothing() -> None:
    status = SessionStatus()
    status.cancel_operation(1)
    clipboard = FakeClipboard()
    audio = FakeAudio()
    ClipboardHandler(audio, clipboard=clipboard).handle("olá", _op(status))  # type: ignore[arg-type]

    assert clipboard.copied == []
    assert audio.transcription_complete_calls == 0
    assert status.result(None, "all")["seq"] == 0
