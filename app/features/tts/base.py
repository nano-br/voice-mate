from __future__ import annotations

from typing import Protocol


class TextToSpeech(Protocol):
    """Text-to-Speech orchestrator.

    Concrete implementations (VoxCPM, Edge-TTS, pyttsx3, etc.) stay isolated in
    their own modules and can be swapped without touching the call flow.
    """

    def is_active(self) -> bool:
        """Indicates whether the speaker is ready to speak.

        The handler uses this signal to decide whether to call `speak` or fall
        back to the alternative audio feedback (beep). `False` for NullSpeaker
        and for speakers whose bootstrap failed.
        """
        ...

    def speak(self, text: str) -> None:
        """Synthesize and play the text. Blocks until done or until `stop()`."""
        ...

    def warmup(self) -> None:
        """Pre-warm the speaker (load model/tune kernels) outside the 1st turn.

        Called in the background at startup so the 1st sentence comes out realtime.
        Idempotent. No-op for NullSpeaker and for disabled speakers.
        """
        ...

    def wait_done(self, timeout: float | None = None) -> bool:
        """Wait for the queued audio to finish playing (end of turn).

        For pipelined speakers (`speak()` enqueues and returns), the handler
        calls this at the end to wait for playback. Speakers that already block
        in `speak()` can return True immediately.
        """
        ...

    def stop(self) -> None:
        """Immediately interrupt the playback in progress."""
        ...

    def close(self) -> None:
        """Release resources (model, streams)."""
        ...


class NullSpeaker:
    """No-op speaker — used when TTS is disabled or failed to start."""

    def is_active(self) -> bool:
        return False

    def speak(self, text: str) -> None:
        return None

    def warmup(self) -> None:
        return None

    def wait_done(self, timeout: float | None = None) -> bool:
        return True

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None
