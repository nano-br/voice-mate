from __future__ import annotations

import pytest

from app.core.transcription_handler import ClipboardHandler


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


def test_clipboard_handler_copies_and_beeps(monkeypatch: pytest.MonkeyPatch) -> None:
    copied: list[str] = []
    monkeypatch.setattr("app.core.transcription_handler.pyperclip.copy", copied.append)
    audio = FakeAudio()
    handler = ClipboardHandler(audio)  # type: ignore[arg-type]

    handler.handle("texto transcrito")

    assert copied == ["texto transcrito"]
    assert audio.transcription_complete_calls == 1


def test_clipboard_handler_is_not_busy_and_cancel_is_noop() -> None:
    audio = FakeAudio()
    handler = ClipboardHandler(audio)  # type: ignore[arg-type]

    assert handler.is_busy() is False
    handler.cancel_in_flight()  # não deve levantar
    handler.close()  # não deve levantar
