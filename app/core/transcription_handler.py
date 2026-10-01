from __future__ import annotations

from typing import Protocol

from app.core.audio_feedback import AudioFeedback
from app.core.session_status import OperationHandle
from app.i18n import _
from app.platform.clipboard import ClipboardWriter, PyperclipWriter


class TranscriptionHandler(Protocol):
    """Decide what to do with the text coming from transcription.

    `op` is the operation being served: results are published through it, and
    its `Publication` says whether the daemon still writes the clipboard and
    plays the cue itself (a client may hold those leases). Without `op` the
    handler does every job itself.
    """

    def handle(self, text: str, op: OperationHandle | None = None) -> None: ...

    def is_busy(self) -> bool: ...

    def cancel_in_flight(self) -> None: ...

    def close(self) -> None: ...


class ClipboardHandler:
    """Publish the raw text as the final transcript: clipboard + the transcription-complete sound."""

    def __init__(self, audio: AudioFeedback, clipboard: ClipboardWriter | None = None) -> None:
        self._audio = audio
        self._clipboard: ClipboardWriter = clipboard if clipboard is not None else PyperclipWriter()

    def handle(self, text: str, op: OperationHandle | None = None) -> None:
        operation = op if op is not None else OperationHandle()
        publication = operation.publish_result(text, "transcript", final=True)
        if not publication.published:
            return  # the operation was cancelled meanwhile
        preview = text[:100] + ("..." if len(text) > 100 else "")
        if publication.write_clipboard:
            self._clipboard.copy(text)
        if publication.play_cue:
            self._audio.transcription_complete()
        if publication.write_clipboard:
            print(_("[VoiceMate] ✓ Copied: {preview}").format(preview=preview))
        else:
            print(
                _("[VoiceMate] ✓ Transcribed (the clipboard lease holder copies it): {preview}").format(preview=preview)
            )

    def is_busy(self) -> bool:
        return False

    def cancel_in_flight(self) -> None:
        return None

    def close(self) -> None:
        return None
