"""The first transcription waits for the background STT warmup (backends are not thread-safe)."""

from __future__ import annotations

import threading
import time

import numpy as np
from numpy.typing import NDArray

from app.features.tts.base import NullSpeaker
from app.main import _AfterWarmup, _start_warmup_thread


class SlowWarmupBackend:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.events: list[str] = []

    def warmup(self) -> None:
        self.events.append("warmup start")
        self.release.wait(timeout=5.0)
        self.events.append("warmup end")

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        self.events.append("transcribe")
        return "ok"


def test_transcription_waits_for_the_warmup() -> None:
    backend = SlowWarmupBackend()
    warm = _start_warmup_thread(backend, NullSpeaker())
    gated = _AfterWarmup(backend, warm)
    results: list[str] = []
    worker = threading.Thread(target=lambda: results.append(gated.transcribe(np.zeros(10, dtype=np.float32))))
    worker.start()
    time.sleep(0.1)
    assert results == []  # still warming up
    backend.release.set()
    worker.join(timeout=5.0)
    assert results == ["ok"]
    assert backend.events == ["warmup start", "warmup end", "transcribe"]


def test_backends_without_warmup_are_ready_at_once() -> None:
    warm = _start_warmup_thread(object(), NullSpeaker())
    assert warm.wait(timeout=5.0)
