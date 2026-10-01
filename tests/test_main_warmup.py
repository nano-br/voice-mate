"""The background warmup signals when the STT is ready (the session's first transcription waits)."""

from __future__ import annotations

import threading

from app.features.tts.base import NullSpeaker
from app.main import _start_warmup_thread


class SlowWarmupBackend:
    def __init__(self) -> None:
        self.release = threading.Event()

    def warmup(self) -> None:
        self.release.wait(timeout=5.0)


def test_the_event_is_set_once_the_stt_warmup_is_over() -> None:
    backend = SlowWarmupBackend()
    warm = _start_warmup_thread(backend, NullSpeaker())
    assert not warm.wait(timeout=0.1)
    backend.release.set()
    assert warm.wait(timeout=5.0)


def test_backends_without_warmup_are_ready_at_once() -> None:
    warm = _start_warmup_thread(object(), NullSpeaker())
    assert warm.wait(timeout=5.0)
