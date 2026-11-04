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
from app.daemon.tts_endpoint import TtsEndpoint
from app.features.tts.external_speaker import ExternalSpeaker
from app.features.tts.pcm import GatedSpeaker
from app.features.tts.provider_launcher import (
    RELAUNCH_AFTER_SECONDS,
    ProviderLauncher,
    provider_env,
    spawn_detached,
)
from app.protocol.tts_provider import ErrorKind, ProviderError


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

    code = 0

    def poll(self) -> int:
        return self.code

    def wait(self) -> int:
        return self.code


class Failed(Done):
    code = 127


class Running:
    def poll(self) -> None:
        return None

    def wait(self) -> None:
        return None


def _refused() -> ProviderError:
    """What the client raises when nothing listens at the provider's address."""
    error = ProviderError("unreachable", "nothing is listening")
    error.__cause__ = ConnectionRefusedError()
    return error


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


def test_the_command_runs_only_when_nothing_listens_at_the_provider_address() -> None:
    answer: list[object] = ["ok"]
    launcher, ran = _launcher(answer)
    assert not launcher.start_if_down()
    answer[0] = "loading"  # it is there: starting a second one would not help
    assert not launcher.start_if_down()
    answer[0] = ProviderError("server", "500")
    assert not launcher.start_if_down()
    slow = ProviderError("unreachable", "did not answer in time")
    slow.__cause__ = TimeoutError()
    answer[0] = slow  # alive but busy, maybe loading its models: a second one would fill the memory
    assert not launcher.start_if_down()
    answer[0] = _refused()
    assert launcher.start_if_down()
    assert ran == ["start-it"]


def test_the_command_runs_at_most_once_a_minute_and_never_while_it_still_runs() -> None:
    clock = Clock()
    answer: list[object] = [_refused()]
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
        raise _refused()

    assert not ProviderLauncher("  ", health).start_if_down()

    def broken(_command: str) -> subprocess.Popen[bytes]:
        raise FileNotFoundError(2, "No such file or directory")

    assert not ProviderLauncher("secret-start --token abc", health, spawn=broken).start_if_down()
    out = capsys.readouterr()
    assert "start command failed" in out.err and "No such file" in out.err
    assert "secret-start" not in out.out + out.err and "abc" not in out.out + out.err


def test_a_command_that_failed_is_reported_with_its_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    clock = Clock()
    launcher, ran = _launcher([_refused()], clock=clock, process=Failed())
    assert launcher.start_if_down()
    clock.now += RELAUNCH_AFTER_SECONDS + 1
    assert launcher.start_if_down()
    assert len(ran) == 2
    assert "exited with code 127" in capsys.readouterr().err


def test_the_provider_does_not_inherit_the_engine_virtual_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    venv = os.path.join(os.sep, "engine", "venv")
    scripts = os.path.join(venv, "Scripts" if sys.platform == "win32" else "bin")
    monkeypatch.setenv("VIRTUAL_ENV", venv)
    monkeypatch.setenv("POETRY_ACTIVE", "1")
    monkeypatch.setenv("MAKEFLAGS", "-j")
    monkeypatch.setenv("PATH", os.pathsep.join([scripts, "/usr/bin"]))
    env = provider_env()
    assert env["PATH"] == "/usr/bin"
    assert not {"VIRTUAL_ENV", "POETRY_ACTIVE", "MAKEFLAGS"} & set(env)


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


def test_a_provider_down_when_the_engine_attaches_its_speaker_does_not_turn_the_voice_off() -> None:
    url = f"http://127.0.0.1:{_free_port()}"  # nothing listens there yet: it is starting with the engine
    speaker = ExternalSpeaker(TTSConfig(engine="external", external_url=url), player=_NoPlayer())  # type: ignore[arg-type]
    speaker.warmup()
    assert not speaker.is_active()  # within the outage
    endpoint = TtsEndpoint()
    endpoint.attach(GatedSpeaker(speaker, endpoint.gate))
    assert endpoint.state == "ready"
    speaker._failed = True  # a provider that can never work is off for good
    endpoint.attach(speaker)
    assert endpoint.state == "off"
    speaker.close()


@pytest.mark.parametrize(
    ("kind", "ready"),
    [("unauthorized", True), ("protocol", True), ("rejected", True), ("server", False), ("unreachable", False)],
)
def test_only_what_can_pass_by_itself_reads_as_loading(
    kind: ErrorKind, ready: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"http://127.0.0.1:{_free_port()}"
    speaker = ExternalSpeaker(TTSConfig(engine="external", external_url=url), player=_NoPlayer())  # type: ignore[arg-type]

    def health() -> str:
        raise ProviderError(kind, "x")

    monkeypatch.setattr(speaker._client, "health", health)
    assert speaker.pcm_ready() is ready
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
