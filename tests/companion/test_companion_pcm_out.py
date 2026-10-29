"""The Windows waveOut output (app/companion/win/pcm_out.py) against a fake winmm, and the
Linux player command lookup. The ctypes structures are plain, so this runs on any platform."""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from app.companion.linux import pcm_out as linux_out
from app.companion.win import pcm_out as win_out
from app.companion.win.pcm_out import WAVERR_STILLPLAYING, WHDR_DONE, WaveOutput

ByRef = Any  # what ctypes.byref returns


class FakeWinmm:
    """Just enough of winmm: queued headers become done on reset, unless `stuck`."""

    def __init__(self) -> None:
        self.queued: list[Any] = []
        self.prepared = 0
        self.unprepared = 0
        self.resets = 0
        self.closes = 0
        self.stuck = False

    @staticmethod
    def _header(ref: ByRef) -> ByRef:
        return ref._obj

    def waveOutOpen(self, *_args: object) -> int:  # noqa: N802
        return 0

    def waveOutPrepareHeader(self, _handle: object, ref: ByRef, _size: int) -> int:  # noqa: N802
        self.prepared += 1
        return 0

    def waveOutWrite(self, _handle: object, ref: ByRef, _size: int) -> int:  # noqa: N802
        self.queued.append(self._header(ref))
        return 0

    def waveOutUnprepareHeader(self, _handle: object, ref: ByRef, _size: int) -> int:  # noqa: N802
        self.unprepared += 1
        return 0

    def waveOutReset(self, _handle: object) -> int:  # noqa: N802
        self.resets += 1
        if not self.stuck:
            for header in self.queued:
                header.dwFlags |= WHDR_DONE
        return 0

    def waveOutClose(self, _handle: object) -> int:  # noqa: N802
        self.closes += 1
        pending = any(not header.dwFlags & WHDR_DONE for header in self.queued)
        return WAVERR_STILLPLAYING if pending else 0


@pytest.fixture
def winmm(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeWinmm]:
    fake = FakeWinmm()
    monkeypatch.setattr(win_out, "_winmm", lambda: fake)
    monkeypatch.setattr(win_out.time, "sleep", lambda _s: None)
    yield fake


def _output(rate: int = 1000) -> WaveOutput:
    output = WaveOutput()
    output.open(rate, 1)  # 100 ms = 100 samples = 200 bytes
    return output


def test_small_chunks_are_held_until_a_block_is_queued_and_commit_sends_the_rest(winmm: FakeWinmm) -> None:
    output = _output()
    output.write(bytes(100))
    assert winmm.prepared == 0 and output.pending()
    output.write(bytes(100))
    assert winmm.prepared == 1
    output.write(bytes(50))
    output.commit()
    assert winmm.prepared == 2


def test_pending_reclaims_finished_buffers(winmm: FakeWinmm) -> None:
    output = _output()
    output.write(bytes(200))
    assert output.pending()
    winmm.queued[0].dwFlags |= WHDR_DONE
    assert not output.pending()
    assert winmm.unprepared == 1


def test_flush_and_close_hand_back_every_header(winmm: FakeWinmm) -> None:
    output = _output()
    output.write(bytes(200))
    output.write(bytes(200))
    output.flush()
    assert winmm.resets == 1 and winmm.unprepared == 2 and not output.pending()
    output.write(bytes(200))
    output.close()
    assert winmm.unprepared == 3 and winmm.closes == 1


def test_write_after_close_is_ignored(winmm: FakeWinmm) -> None:
    output = _output()
    output.close()
    output.write(bytes(400))
    output.commit()
    output.close()
    assert winmm.prepared == 0 and winmm.closes == 1


def test_buffers_stay_alive_when_the_device_will_not_close(winmm: FakeWinmm) -> None:
    output = _output()
    output.write(bytes(200))
    winmm.stuck = True
    output.close()
    assert winmm.closes == win_out.CLOSE_TRIES
    # The driver may still read them: they must not be freed with the output's own list.
    assert len(output._stranded) == 1
    header, buffer = output._stranded[0]
    assert ctypes.sizeof(buffer) == 200 and header.lpData is not None
    assert output._buffers == []


def test_player_command_prefers_pw_cat_and_never_falls_back_to_paplay(monkeypatch: pytest.MonkeyPatch) -> None:
    found: dict[str, str] = {"pw-cat": "/usr/bin/pw-cat", "pacat": "/usr/bin/pacat", "paplay": "/usr/bin/paplay"}
    monkeypatch.setattr(linux_out.shutil, "which", lambda name: found.get(name))
    assert linux_out.player_command(24000, 1)[0] == "/usr/bin/pw-cat"  # type: ignore[index]
    del found["pw-cat"]
    command = linux_out.player_command(24000, 1)
    assert command is not None and command[0] == "/usr/bin/pacat" and "--playback" in command
    del found["pacat"]
    assert linux_out.player_command(24000, 1) is None  # paplay has no --playback: not a substitute


class _BlockedPipe:
    """A player's stdin whose pipe is full: `write` blocks until the process is killed."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.killed = threading.Event()

    def write(self, _data: bytes) -> int:
        self.entered.set()
        self.killed.wait(5)
        raise BrokenPipeError("the player is gone")

    def flush(self) -> None: ...

    def close(self) -> None: ...


class _FakePlayer:
    def __init__(self, stdin: _BlockedPipe) -> None:
        self.stdin = stdin

    def kill(self) -> None:
        self.stdin.killed.set()

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_flush_does_not_wait_for_a_write_blocked_on_a_full_pipe() -> None:
    output = linux_out.PipeOutput()
    pipe = _BlockedPipe()
    output._process = _FakePlayer(pipe)  # type: ignore[assignment]
    output._bytes_per_s = 48000
    errors: list[BaseException] = []

    def write() -> None:
        try:
            output.write(bytes(16384))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    assert pipe.entered.wait(2)
    started = time.monotonic()
    output.flush()
    assert time.monotonic() - started < 0.5
    writer.join(2)
    assert not writer.is_alive()
    assert errors == []  # killed on purpose: not an error
