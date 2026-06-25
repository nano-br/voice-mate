"""Recorder: stop()×callback deadlock regression and basic contracts.

The InputStream fake mimics PortAudio's real behavior: `stream.stop()` blocks
until the in-flight callback returns — here, by invoking the callback
synchronously inside stop(). With the old code (stream.stop() holding
`self._lock`), this deadlocked: the callback waited on the lock and stop waited
on the callback (visible under WSLg, where the PulseAudio-RDP callbacks are slow).
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pytest

from app.core.recorder import Recorder


class _FalsyStatus:
    def __bool__(self) -> bool:
        return False


class _FakeInputStream:
    """Captures the callback and re-invokes it inside stop() (as PortAudio does)."""

    last_instance: _FakeInputStream | None = None

    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401 — mirrors the sounddevice API
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False
        type(self).last_instance = self

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        # PortAudio waits for the in-flight callback to complete before returning.
        chunk = np.ones((160, 1), dtype=np.float32)
        self.callback(chunk, 160, None, _FalsyStatus())
        self.stopped = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeInputStream.last_instance = None
    monkeypatch.setattr("app.core.recorder.sd.InputStream", _FakeInputStream)


def test_stop_does_not_deadlock_with_inflight_callback() -> None:
    recorder = Recorder(sample_rate=16000)
    assert recorder.start() is True
    stream = _FakeInputStream.last_instance
    assert stream is not None
    # A "normal" chunk during recording.
    stream.callback(np.ones((160, 1), dtype=np.float32), 160, None, _FalsyStatus())

    result: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: result.update(audio=recorder.stop()), daemon=True)
    worker.start()
    worker.join(timeout=5.0)

    assert not worker.is_alive(), "Recorder.stop() deadlockou com callback em voo"
    audio = result["audio"]
    assert audio is not None
    # 1 chunk during recording + 1 delivered by the in-flight callback in stop().
    assert len(audio) == 320
    assert stream.stopped and stream.closed


def test_stop_without_start_returns_none() -> None:
    recorder = Recorder(sample_rate=16000)
    assert recorder.stop() is None


def test_start_twice_returns_false() -> None:
    recorder = Recorder(sample_rate=16000)
    assert recorder.start() is True
    assert recorder.start() is False


def test_stop_with_no_chunks_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SilentStream(_FakeInputStream):
        def stop(self) -> None:  # no in-flight callback, no audio
            self.stopped = True

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _SilentStream)
    recorder = Recorder(sample_rate=16000)
    assert recorder.start() is True
    assert recorder.stop() is None
