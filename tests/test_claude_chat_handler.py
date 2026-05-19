from __future__ import annotations

import threading
import time

import pytest

from app.services.claude_chat_handler import ClaudeChatHandler


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

    def is_active(self) -> bool:
        return self._active

    def speak(self, text: str) -> None:
        self.speak_calls.append(text)

    def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1


def _handler(runtime: FakeRuntime, audio: FakeAudio, speaker: FakeSpeaker) -> ClaudeChatHandler:
    return ClaudeChatHandler(runtime, audio, speaker)  # type: ignore[arg-type]


def test_handle_copies_transcription_then_response(monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []
    monkeypatch.setattr("app.services.claude_chat_handler.pyperclip.copy", copied.append)
    runtime = FakeRuntime(response="resposta")
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker)

    handler.handle("pergunta")

    assert runtime.send_calls == ["pergunta"]
    assert copied == ["pergunta", "resposta"]
    assert audio.ai_response_ready_calls == 1
    assert speaker.speak_calls == []
    assert audio.error_calls == 0
    assert handler.is_busy() is False


def test_handle_with_active_speaker_uses_tts_instead_of_beep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied: list[str] = []
    monkeypatch.setattr("app.services.claude_chat_handler.pyperclip.copy", copied.append)
    runtime = FakeRuntime(response="resposta falada")
    audio = FakeAudio()
    speaker = FakeSpeaker(active=True)
    handler = _handler(runtime, audio, speaker)

    handler.handle("pergunta")

    assert copied == ["pergunta", "resposta falada"]
    assert speaker.speak_calls == ["resposta falada"]
    assert audio.ai_response_ready_calls == 0


def test_handle_copies_transcription_before_calling_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        "app.services.claude_chat_handler.pyperclip.copy",
        lambda value: events.append(f"copy:{value}"),
    )

    class OrderingRuntime(FakeRuntime):
        def send_and_collect(self, prompt: str, timeout: float | None = None) -> str:
            events.append(f"send:{prompt}")
            return "resposta"

    runtime = OrderingRuntime()
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker)

    handler.handle("pergunta")

    assert events == ["copy:pergunta", "send:pergunta", "copy:resposta"]


def test_handle_exception_keeps_transcription_in_clipboard(monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []
    monkeypatch.setattr("app.services.claude_chat_handler.pyperclip.copy", copied.append)
    runtime = FakeRuntime(raise_exc=RuntimeError("boom"))
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker)

    handler.handle("pergunta")

    assert copied == ["pergunta"]
    assert audio.error_calls == 1
    assert audio.ai_response_ready_calls == 0
    assert handler.is_busy() is False


def test_cancel_in_flight_keeps_transcription_discards_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied: list[str] = []
    monkeypatch.setattr("app.services.claude_chat_handler.pyperclip.copy", copied.append)
    block_event = threading.Event()
    runtime = FakeRuntime(response="resposta tardia", block_event=block_event)
    audio = FakeAudio()
    speaker = FakeSpeaker(active=False)
    handler = _handler(runtime, audio, speaker)

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
    assert copied == ["pergunta"]
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
