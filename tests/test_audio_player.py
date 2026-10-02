from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from app.features.tts.audio_player import AudioPlayer


class FakeOutputStream:
    """Fake of sounddevice.OutputStream for the AudioPlayer tests."""

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

    def factory(**kwargs: Any) -> FakeOutputStream:  # noqa: ANN401 — sounddevice kwargs
        stream = FakeOutputStream(
            samplerate=kwargs["samplerate"],
            channels=kwargs["channels"],
            dtype=kwargs["dtype"],
            callback=kwargs["callback"],
        )
        captured["stream"] = stream
        captured["kwargs"] = kwargs
        return stream

    monkeypatch.setattr("app.features.tts.audio_player.sd.OutputStream", factory)
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


def test_explicit_blocksize_latency_passed_to_stream(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer(blocksize=4096, latency=0.2)
    player.start(24000)
    assert fake_sd["kwargs"]["blocksize"] == 4096
    assert fake_sd["kwargs"]["latency"] == 0.2


def test_default_params_wsl2_use_bigger_buffer(fake_sd: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.features.tts.audio_player.detect_platform", lambda: "wsl2")
    player = AudioPlayer()
    player.start(24000)
    assert fake_sd["kwargs"]["blocksize"] == 4096
    assert fake_sd["kwargs"]["latency"] == 0.2


def test_default_params_windows_keep_portaudio_default(
    fake_sd: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.features.tts.audio_player.detect_platform", lambda: "windows")
    player = AudioPlayer()
    player.start(24000)
    assert fake_sd["kwargs"]["blocksize"] == 0
    assert fake_sd["kwargs"]["latency"] is None


def test_underflow_logged_once_per_stream(fake_sd: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    player = AudioPlayer()
    player.start(24000)
    stream = fake_sd["stream"]
    # Several consecutive callbacks with a truthy status (underflow).
    for _ in range(5):
        outdata: NDArray[np.float32] = np.zeros((512, 1), dtype=np.float32)
        stream.callback(outdata, 512, None, 1)  # status truthy = underflow
    err = capsys.readouterr().err
    assert err.count("[AudioPlayer]") == 1  # logged only once despite the 5 underflows


def test_ensure_started_opens_once_and_reuses(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.ensure_started(24000)
    first = fake_sd["stream"]
    player.ensure_started(24000)  # same rate → does NOT reopen (persistent stream)
    assert fake_sd["stream"] is first
    assert first.stopped is False
    assert first.closed is False


def test_ensure_started_reopens_on_rate_change(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.ensure_started(24000)
    first = fake_sd["stream"]
    player.ensure_started(48000)  # different rate → reopens
    assert fake_sd["stream"] is not first
    assert fake_sd["stream"].samplerate == 48000
    assert first.stopped is True


def test_ensure_started_reopens_after_abort(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.ensure_started(24000)
    first = fake_sd["stream"]
    player.abort()
    player.ensure_started(24000)  # after abort, needs a fresh stream
    assert fake_sd["stream"] is not first


def test_feed_and_callback_drains_queue(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]

    chunk: NDArray[np.float32] = np.ones(4, dtype=np.float32) * 0.5
    player.feed(chunk)

    out = _drive_callback(stream, 4)
    assert np.allclose(out[:, 0], [0.5, 0.5, 0.5, 0.5])
    # after consuming, the next callback signals idle and fills silence
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
    # remainder of the chunk + silence
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

    # callback after abort produces silence
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
    # before the callback consumes, drain must not have returned yet
    time.sleep(0.05)
    assert drained.is_set() is False

    # consume everything
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
    # after abort, this specific stream is closed — feed plays nothing
    assert stream.closed is True
    player.feed(np.ones(4, dtype=np.float32))


def test_abort_closes_stream_zeros_reference(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    stream = fake_sd["stream"]
    player.abort()
    assert stream.aborted is True
    assert stream.closed is True


def test_start_after_abort_creates_fresh_stream_and_clears_aborted(
    fake_sd: dict[str, Any],
) -> None:
    """Regression for the bug where start() was a no-op after abort(), dropping future feeds."""
    player = AudioPlayer()
    player.start(48000)
    first_stream = fake_sd["stream"]
    player.abort()
    assert first_stream.closed is True

    # a new start must create a completely fresh stream
    player.start(48000)
    second_stream = fake_sd["stream"]
    assert second_stream is not first_stream
    assert second_stream.started is True

    # feed after a fresh start must work again
    player.feed(np.array([1.0, 0.5, 0.25, 0.125], dtype=np.float32))
    out = _drive_callback(second_stream, 4)
    assert np.allclose(out[:, 0], [1.0, 0.5, 0.25, 0.125])


def test_start_replaces_existing_stream(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    first_stream = fake_sd["stream"]
    player.start(48000)
    second_stream = fake_sd["stream"]
    assert second_stream is not first_stream
    assert first_stream.closed is True
    assert second_stream.started is True


def test_drain_default_timeout_returns_quickly_when_idle(fake_sd: dict[str, Any]) -> None:
    player = AudioPlayer()
    player.start(48000)
    # nothing queued → drain returns True immediately with the default
    assert player.drain() is True
