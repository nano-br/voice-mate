"""AudioServerProbe: maps `pactl info` outcomes to ok/down/unknown for /health."""

from __future__ import annotations

import subprocess

import pytest

from app.platform.audio_probe import AudioServerProbe


@pytest.fixture
def _has_pactl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.platform.audio_probe.shutil.which", lambda _name: "/usr/bin/pactl")


@pytest.mark.usefixtures("_has_pactl")
def test_answering_server_is_ok() -> None:
    probe = AudioServerProbe(runner=lambda _cmd, _timeout: 0)
    assert probe.probe_once() == "ok"
    assert probe.state == "ok"


@pytest.mark.usefixtures("_has_pactl")
def test_error_exit_is_down() -> None:
    probe = AudioServerProbe(runner=lambda _cmd, _timeout: 1)
    assert probe.probe_once() == "down"


@pytest.mark.usefixtures("_has_pactl")
def test_hanging_server_is_down() -> None:
    """The WSLg failure mode: the server accepts nothing and pactl hangs → timeout."""

    def _hang(cmd: list[str], timeout: float) -> int:
        raise subprocess.TimeoutExpired(cmd, timeout)

    probe = AudioServerProbe(runner=_hang)
    assert probe.probe_once() == "down"


def test_without_pactl_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.platform.audio_probe.shutil.which", lambda _name: None)
    probe = AudioServerProbe(runner=lambda _cmd, _timeout: 0)
    assert probe.probe_once() == "unknown"
