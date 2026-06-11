from __future__ import annotations

from typing import Protocol

from app.core.audio_feedback import AudioFeedback
from app.platform.clipboard import ClipboardWriter, PyperclipWriter


class TranscriptionHandler(Protocol):
    """Decide o que fazer com o texto vindo da transcrição."""

    def handle(self, text: str) -> None: ...

    def is_busy(self) -> bool: ...

    def cancel_in_flight(self) -> None: ...

    def close(self) -> None: ...


class ClipboardHandler:
    """Copia o texto cru para o clipboard e toca o som de transcrição concluída."""

    def __init__(self, audio: AudioFeedback, clipboard: ClipboardWriter | None = None) -> None:
        self._audio = audio
        self._clipboard: ClipboardWriter = clipboard if clipboard is not None else PyperclipWriter()

    def handle(self, text: str) -> None:
        self._clipboard.copy(text)
        self._audio.transcription_complete()
        preview = text[:100] + ("..." if len(text) > 100 else "")
        print(f"[VoiceMate] ✓ Copiado: {preview}")

    def is_busy(self) -> bool:
        return False

    def cancel_in_flight(self) -> None:
        return None

    def close(self) -> None:
        return None
