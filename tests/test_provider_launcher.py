"""ProviderLauncher: the engine runs the provider's start command only when the provider does not
answer, at most once a minute and never while what it started is still running; and the
ExternalSpeaker asks for it when it is built and whenever the provider is unreachable."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.cli.args import parse_args
from app.cli.config_builder import build_config
from app.core.config import TTSConfig
from app.features.tts.external_speaker import ExternalSpeaker
from app.features.tts.provider_launcher import RELAUNCH_AFTER_SECONDS, ProviderLauncher, spawn_detached
from app.protocol.tts_provider import ProviderError


def wait_until(condition: object, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():  # type: ignore[operator]
            return True
        time.sleep(0.01)
    return False


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Done:
    """A started process that already ended (the command forked the provider and returned)."""

    def poll(self) -> int:
        return 0


class Running:
    def poll(self) -> None:
        return None


def _launcher(
    answer: list[object], *, clock: Clock | None = None, process: object = None
) -> tuple[ProviderLauncher, list[str]]:
    ran: list[str] = []

    def health() -> str:
        if isinstance(answer[0], ProviderError):
            raise answer[0]
        return str(answer[0])

    def spawn(command: str) -> subprocess.Popen[bytes]:
        ran.append(command)
        return process if process is not None else Done()  # type: ignore[return-value]

    return ProviderLauncher(" start-it ", health, spawn=spawn, clock=clock or Clock()), ran


def test_the_command_runs_only_when_the_provider_is_unreachable() -> None:
    answer: list[object] = ["ok"]
    launcher, ran = _launcher(answer)
    assert not launcher.start_if_down()
    answer[0] = "loading"  # it is there: starting a second one would not help
    assert not launcher.start_if_down()
    answer[0] = ProviderError("server", "500")
    assert not launcher.start_if_down()
    answer[0] = ProviderError("unreachable", "connection refused")
    assert launcher.start_if_down()
    assert ran == ["start-it"]


def test_the_command_runs_at_most_once_a_minute_and_never_while_it_still_runs() -> None:
    clock = Clock()
    answer: list[object] = [ProviderError("unreachable", "refused")]
    launcher, ran = _launcher(answer, clock=clock)
    assert launcher.start_if_down()
    clock.now += RELAUNCH_AFTER_SECONDS - 1
    assert not launcher.start_if_down()
    clock.now += 2
    assert launcher.start_if_down()
    assert len(ran) == 2
    running, ran_running = _launcher(answer, clock=clock, process=Running())
    assert running.start_if_down()
    clock.now += RELAUNCH_AFTER_SECONDS * 10
    assert not running.start_if_down()  # what it started is still opening the provider
    assert len(ran_running) == 1


def test_without_a_command_nothing_runs_and_a_failed_command_is_reported_without_itself(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def health() -> str:
        raise ProviderError("unreachable", "refused")

    assert not ProviderLauncher("  ", health).start_if_down()

    def broken(_command: str) -> subprocess.Popen[bytes]:
        raise FileNotFoundError(2, "No such file or directory")

    assert not ProviderLauncher("secret-start --token abc", health, spawn=broken).start_if_down()
    out = capsys.readouterr()
    assert "start command failed" in out.err and "No such file" in out.err
    assert "secret-start" not in out.out + out.err and "abc" not in out.out + out.err


@pytest.mark.skipif(sys.platform == "win32", reason="the engine's shell on Linux and WSL")
def test_the_command_runs_through_the_shell_detached_from_the_engine(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    script = "import os; print(os.getpid(), os.getsid(0))"
    process = spawn_detached(f"exec '{sys.executable}' -c '{script}' > '{marker}'")
    assert process.wait(10) == 0
    pid, session = marker.read_text().split()
    assert pid == session != str(os.getsid(0))  # its own session: closing the engine does not stop it


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_the_speaker_starts_the_provider_when_built_and_when_it_is_unreachable_again() -> None:
    started: list[str] = []
    url = f"http://127.0.0.1:{_free_port()}"  # nothing listens there
    speaker = ExternalSpeaker(TTSConfig(engine="external", external_url=url), player=_NoPlayer())  # type: ignore[arg-type]
    clock = Clock()
    def spawn(command: str) -> subprocess.Popen[bytes]:
        started.append(command)
        return Done()  # type: ignore[return-value]

    launcher = ProviderLauncher("start-it", speaker._client.health, spawn=spawn, clock=clock)
    speaker._launcher = launcher
    launcher.start_soon()
    assert wait_until(lambda: started == ["start-it"])
    assert not speaker.pcm_ready()  # still down, but asked again too soon: nothing new runs
    time.sleep(0.2)
    assert started == ["start-it"]
    clock.now += RELAUNCH_AFTER_SECONDS + 1
    assert not speaker.pcm_ready()
    assert wait_until(lambda: started == ["start-it", "start-it"])
    speaker.close()


def test_the_start_command_comes_from_the_environment_and_is_taken_out_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICEMATE_TTS_START_COMMAND", " start-it ")
    tts = build_config(parse_args(["--tts-engine", "external"])).tts
    assert tts.external_start_command == "start-it"
    assert "VOICEMATE_TTS_START_COMMAND" not in os.environ
    assert "start-it" not in repr(tts)


class _NoPlayer:
    def ensure_started(self, sample_rate: int) -> None: ...

    def start(self, sample_rate: int) -> None: ...

    def feed(self, chunk: object) -> None: ...

    def drain(self, timeout: float | None = 60.0) -> bool:
        return True

    def abort(self) -> None: ...

    def close(self) -> None: ...
