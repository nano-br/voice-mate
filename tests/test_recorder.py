"""Recorder: async mic-open, stop()×callback deadlock regression, and basic contracts.

The InputStream fake mimics PortAudio's real behavior: `stream.stop()` blocks
until the in-flight callback returns — here, by invoking the callback
synchronously inside stop(). With the old code (stream.stop() holding
`self._lock`), this deadlocked: the callback waited on the lock and stop waited
on the callback (visible under WSLg, where the PulseAudio-RDP callbacks are slow).

The capture stream is opened off-thread, so tests wait for it to come up before
acting on it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from app.core.recorder import Recorder


def _wait(pred: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return False


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
    assert _wait(lambda: recorder._stream is not None)  # background open completed
    stream = _FakeInputStream.last_instance
    assert stream is not None
    # A "normal" chunk during recording.
    stream.callback(np.ones((160, 1), dtype=np.float32), 160, None, _FalsyStatus())

    result: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: result.update(audio=recorder.stop()), daemon=True)
    worker.start()
    worker.join(timeout=5.0)

    assert not worker.is_alive(), "Recorder.stop() deadlocked with an in-flight callback"
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


def test_mic_open_failure_reverts_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the capture stream can't open (no mic / WSLg PulseAudio down), start() still
    returns True without blocking; the off-thread open fails, reverts to not-recording
    and reports the reason via on_failure, and a later start works."""

    class _FailingStream(_FakeInputStream):
        def start(self) -> None:
            raise RuntimeError("PulseAudio: Unable to create stream: Timeout")

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _FailingStream)
    recorder = Recorder(sample_rate=16000)
    reasons: list[str] = []

    assert recorder.start(on_failure=reasons.append) is True  # optimistic: never blocks on the mic
    assert _wait(lambda: bool(reasons))
    assert recorder.is_recording is False
    assert "Unable to create stream" in reasons[0]

    # State is clean → a later start (once the mic is back) still works.
    monkeypatch.setattr("app.core.recorder.sd.InputStream", _FakeInputStream)
    assert recorder.start() is True
    assert _wait(lambda: recorder._stream is not None)


def test_mic_open_failure_without_callback_prints(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _FailingStream(_FakeInputStream):
        def start(self) -> None:
            raise RuntimeError("no device")

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _FailingStream)
    recorder = Recorder(sample_rate=16000)
    assert recorder.start() is True
    assert _wait(lambda: recorder.is_recording is False)
    assert _wait(lambda: "microphone" in capsys.readouterr().err)


def test_hung_open_times_out_then_fails_fast_until_it_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead audio server makes the open HANG (not fail). The timeout guard reports it
    and reverts; while that open is still stuck, further starts fail fast instead of
    stacking more hung opens; once it finally returns, the late stream is discarded."""
    release = threading.Event()
    streams: list[_FakeInputStream] = []

    class _HangingStream(_FakeInputStream):
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            super().__init__(**kwargs)
            streams.append(self)

        def start(self) -> None:
            release.wait(timeout=5.0)
            self.started = True

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _HangingStream)
    recorder = Recorder(sample_rate=16000, open_timeout=0.1)
    reasons: list[str] = []

    assert recorder.start(on_failure=reasons.append) is True
    assert _wait(lambda: len(reasons) == 1)
    assert "timed out" in reasons[0]
    assert recorder.is_recording is False

    # Still stuck → the next start fails fast, without a second open.
    assert recorder.start(on_failure=reasons.append) is True
    assert _wait(lambda: len(reasons) == 2)
    assert "not responding" in reasons[1]
    assert len(streams) == 1

    # The hung open finally returns: it must not attach to anything (nobody is recording).
    release.set()
    assert _wait(lambda: streams[0].closed)
    assert recorder.is_recording is False
    assert len(reasons) == 2  # no duplicate report for the timed-out generation


def test_guard_that_loses_the_race_to_the_open_reports_nothing() -> None:
    """The timeout guard firing right after the stream attached must not report a
    failure (it used to: the session went idle while the recorder kept capturing)."""
    recorder = Recorder(sample_rate=16000, open_timeout=60.0)
    reasons: list[str] = []
    assert recorder.start(on_failure=reasons.append) is True
    assert _wait(lambda: recorder._stream is not None)

    recorder._open_timed_out(recorder._generation, reasons.append)  # guard thread arriving late
    recorder._fail(recorder._generation, reasons.append, "late")  # or a late failure path

    assert reasons == []
    assert recorder.is_recording is True


def test_quick_start_stop_start_is_not_mistaken_for_a_stuck_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """A double tap while a HEALTHY (just slow) open is in flight must not report
    "not responding": only an open older than the timeout means the server is stuck."""
    release = threading.Event()

    class _SlowStream(_FakeInputStream):
        def start(self) -> None:
            release.wait(timeout=5.0)
            self.started = True

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _SlowStream)
    recorder = Recorder(sample_rate=16000, open_timeout=5.0)
    reasons: list[str] = []

    assert recorder.start(on_failure=reasons.append) is True
    assert recorder.stop() is None
    assert recorder.start(on_failure=reasons.append) is True  # 2nd open while the 1st is in flight
    release.set()
    assert _wait(lambda: recorder._stream is not None)
    assert reasons == []
    assert recorder.is_recording is True


def test_on_opened_fires_after_attach_and_never_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[bool] = []
    recorder = Recorder(sample_rate=16000)
    assert recorder.start(on_opened=lambda: opened.append(recorder._stream is not None)) is True
    assert _wait(lambda: opened == [True])  # the stream was already attached when it fired
    recorder.stop()

    class _FailingStream(_FakeInputStream):
        def start(self) -> None:
            raise RuntimeError("no device")

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _FailingStream)
    reasons: list[str] = []
    assert recorder.start(on_failure=reasons.append, on_opened=lambda: opened.append(False)) is True
    assert _wait(lambda: bool(reasons))
    assert opened == [True]


def test_stream_that_fails_to_start_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Created but failed in start(): the PortAudio handle must be released."""

    class _FailingStream(_FakeInputStream):
        def start(self) -> None:
            raise RuntimeError("Pa_StartStream failed")

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _FailingStream)
    recorder = Recorder(sample_rate=16000)
    reasons: list[str] = []
    assert recorder.start(on_failure=reasons.append) is True
    assert _wait(lambda: bool(reasons))
    assert _FailingStream.last_instance is not None
    assert _FailingStream.last_instance.closed is True


def test_hung_open_is_reported_even_if_the_user_already_pressed_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dead audio server: the user presses start, hears nothing and presses stop before
    the timeout. The hang must still be reported (it used to be silent)."""
    release = threading.Event()

    class _HangingStream(_FakeInputStream):
        def start(self) -> None:
            release.wait(timeout=5.0)

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _HangingStream)
    recorder = Recorder(sample_rate=16000, open_timeout=0.2)
    reasons: list[str] = []
    assert recorder.start(on_failure=reasons.append) is True
    assert recorder.stop() is None  # stop pressed while the mic was still opening
    assert _wait(lambda: bool(reasons))
    release.set()
    assert "timed out" in reasons[0]


def test_audio_from_a_stale_stream_never_reaches_a_newer_recording() -> None:
    recorder = Recorder(sample_rate=16000)
    assert recorder.start() is True
    assert _wait(lambda: recorder._stream is not None)
    stale_callback = _FakeInputStream.last_instance.callback if _FakeInputStream.last_instance else None
    assert stale_callback is not None
    recorder.stop()

    assert recorder.start() is True  # newer generation
    assert _wait(lambda: recorder._stream is not None)
    stale_callback(np.full((160, 1), 9.0, dtype=np.float32), 160, None, _FalsyStatus())
    audio = recorder.stop()
    assert audio is not None
    assert not np.any(audio == 9.0)  # only the newer stream's own chunk (delivered in stop)


def test_stop_before_open_completes_discards_late_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    class _SlowStream(_FakeInputStream):
        def start(self) -> None:
            release.wait(timeout=5.0)
            self.started = True

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _SlowStream)
    recorder = Recorder(sample_rate=16000)
    assert recorder.start() is True
    assert recorder.stop() is None  # pressed stop while the mic was still opening
    release.set()
    assert _wait(lambda: _SlowStream.last_instance is not None and _SlowStream.last_instance.closed)
    assert recorder.is_recording is False


def test_stop_with_no_chunks_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SilentStream(_FakeInputStream):
        def stop(self) -> None:  # no in-flight callback, no audio
            self.stopped = True

    monkeypatch.setattr("app.core.recorder.sd.InputStream", _SilentStream)
    recorder = Recorder(sample_rate=16000)
    assert recorder.start() is True
    assert _wait(lambda: recorder._stream is not None)
    assert recorder.stop() is None
