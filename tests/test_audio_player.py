from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from app.services.audio_player import AudioPlayer


class FakeOutputStream:
    """Fake da sounddevice.OutputStream para os testes do AudioPlayer."""

    def __init__(
        self,
        samplerate: int,
        channels: int,
        dtype: str,
        callback: Callable[..., None],
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.callback = callback
        self.started = False
        self.stopped = False
        self.aborted = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_sd(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeOutputStream:  # noqa: ANN401 — kwargs do sounddevice
        stream = FakeOutputStream(
            samplerate=kwargs["samplerate"],
            channels=kwargs["channels"],
            dtype=kwargs["dtype"],
            callback=kwargs["callback"],
        )
        captured["stream"] = stream
        return stream

    monkeypatch.setattr("app.services.audio_player.sd.OutputStream", factory)
    return captured


def _drive_callback(stream: FakeOutputStream, frames: int) -> NDArray[np.float32]:
    outdata: NDArray[np.float32] = np.zeros((frames, 1), dtype=np.float32)
    stream.callback(outdata, frames, None, 0)
    return outdata


def test_start_creates_stream(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)

    stream = fake_sd["stream"]
    assert stream.samplerate == 48000
    assert stream.channels == 1
    assert stream.started is True


def test_feed_and_callback_drains_queue(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]

    chunk: NDArray[np.float32] = np.ones(4, dtype=np.float32) * 0.5
    player.feed(chunk)

    out = _drive_callback(stream, 4)
    assert np.allclose(out[:, 0], [0.5, 0.5, 0.5, 0.5])
    # após consumir, callback subsequente sinaliza idle e preenche silêncio
    out2 = _drive_callback(stream, 4)
    assert np.allclose(out2[:, 0], 0.0)
    assert player.drain(timeout=0.5) is True


def test_callback_splits_chunk_across_calls(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]

    chunk: NDArray[np.float32] = np.arange(6, dtype=np.float32)
    player.feed(chunk)

    out1 = _drive_callback(stream, 4)
    assert np.allclose(out1[:, 0], [0.0, 1.0, 2.0, 3.0])
    out2 = _drive_callback(stream, 4)
    # restante do chunk + silêncio
    assert np.allclose(out2[:2, 0], [4.0, 5.0])
    assert np.allclose(out2[2:, 0], 0.0)


def test_abort_drains_queue_and_signals(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]

    player.feed(np.ones(1024, dtype=np.float32))
    player.feed(np.ones(1024, dtype=np.float32))

    player.abort()

    assert stream.aborted is True
    assert player.drain(timeout=0.5) is True

    # callback após abort produz silêncio
    out = _drive_callback(stream, 4)
    assert np.allclose(out[:, 0], 0.0)


def test_drain_blocks_until_callback_marks_idle(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]

    player.feed(np.ones(4, dtype=np.float32))
    drained = threading.Event()

    def waiter() -> None:
        if player.drain(timeout=2.0):
            drained.set()

    threading.Thread(target=waiter, daemon=True).start()
    # antes do callback consumir, drain ainda não deve ter retornado
    time.sleep(0.05)
    assert drained.is_set() is False

    # consumir tudo
    _drive_callback(stream, 4)
    _drive_callback(stream, 4)

    assert drained.wait(timeout=1.0) is True


def test_close_stops_and_closes_stream(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]

    player.close()

    assert stream.stopped is True
    assert stream.closed is True


def test_feed_after_abort_is_ignored(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]
    player.abort()
    player.feed(np.ones(4, dtype=np.float32))
    out = _drive_callback(stream, 4)
    assert np.allclose(out[:, 0], 0.0)
