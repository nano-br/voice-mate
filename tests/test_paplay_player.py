"""PaplayPlayer: writes PCM to paplay via a thread, without deadlock (timeouts)."""

from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np
import pytest

import app.features.tts.paplay_player as pp_mod
from app.features.tts.paplay_player import PaplayPlayer


class FakeStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = False

    def write(self, b: bytes) -> int:
        self.data += b
        return len(b)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeProc:
    def __init__(self, cmd: list[str], **kwargs: Any) -> None:  # noqa: ANN401
        self.cmd = cmd
        self.stdin = FakeStdin()
        self._terminated = False
        self._wait_event = threading.Event()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        # "drains" when stdin is closed (writer finished); otherwise waits for the signal.
        deadline = time.monotonic() + (timeout or 5.0)
        while time.monotonic() < deadline:
            if self.stdin.closed or self._terminated:
                self.returncode = 0
                return 0
            time.sleep(0.005)
        import subprocess

        raise subprocess.TimeoutExpired(self.cmd, timeout or 0)

    def terminate(self) -> None:
        self._terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self._terminated = True
        self.returncode = -9


@pytest.fixture(autouse=True)
def _fake_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp_mod.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(pp_mod.shutil, "which", lambda name: "/usr/bin/paplay")


def test_start_spawns_paplay_with_rate() -> None:
    player = PaplayPlayer()
    player.start(24000)
    assert player._proc is not None
    assert "--rate=24000" in player._proc.cmd  # type: ignore[attr-defined]
    assert "--format=float32le" in player._proc.cmd  # type: ignore[attr-defined]
    player.close()


def test_feed_writes_pcm_then_drain() -> None:
    player = PaplayPlayer()
    player.ensure_started(24000)
    proc: Any = player._proc
    player.feed(np.ones(100, dtype=np.float32))
    player.feed(np.full(50, 0.5, dtype=np.float32))
    assert player.drain(timeout=5.0) is True
    assert len(proc.stdin.data) == (100 + 50) * 4  # float32 = 4 bytes
    assert proc.stdin.closed is True


def test_ensure_started_reuses_same_rate() -> None:
    player = PaplayPlayer()
    player.ensure_started(24000)
    first = player._proc
    player.ensure_started(24000)
    assert player._proc is first  # same rate → does not reopen
    player.close()


def test_abort_kills_process_and_is_idempotent() -> None:
    player = PaplayPlayer()
    player.ensure_started(24000)
    proc: Any = player._proc
    player.abort()
    assert proc._terminated is True
    assert player._proc is None
    player.abort()  # idempotent, does not raise


def test_drain_without_start_returns_true() -> None:
    assert PaplayPlayer().drain() is True


def test_paplay_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pp_mod.shutil, "which", lambda name: None)
    assert pp_mod.paplay_available() is False
    monkeypatch.setattr(pp_mod.shutil, "which", lambda name: "/usr/bin/paplay")
    assert pp_mod.paplay_available() is True


def test_factory_picks_paplay_on_wsl2(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.features.tts.audio_player as ap_mod

    monkeypatch.setattr(ap_mod, "detect_platform", lambda: "wsl2")
    monkeypatch.setattr(pp_mod.shutil, "which", lambda name: "/usr/bin/paplay")
    assert isinstance(ap_mod.create_audio_player(), PaplayPlayer)


def test_factory_falls_back_to_sounddevice_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.features.tts.audio_player as ap_mod

    monkeypatch.setattr(ap_mod, "detect_platform", lambda: "windows")
    assert isinstance(ap_mod.create_audio_player(), ap_mod.AudioPlayer)


def test_factory_falls_back_when_no_paplay(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.features.tts.audio_player as ap_mod

    monkeypatch.setattr(ap_mod, "detect_platform", lambda: "wsl2")
    monkeypatch.setattr(pp_mod.shutil, "which", lambda name: None)
    assert isinstance(ap_mod.create_audio_player(), ap_mod.AudioPlayer)
