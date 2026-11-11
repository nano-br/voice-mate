"""Writing to a terminal, IDE or Remote Control session: only a closed one, resumed in print mode."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.companion.assistant.cli_resume import PROMPT_HEAD, CliResumer, ResumeError, resume_command
from app.companion.assistant.session_reader import LiveSession, SessionStatus

NOW = 1_800_000_000.0


class FakeProcess:
    """A started CLI: it runs until `finish` (or `kill`)."""

    def __init__(self) -> None:
        self.code: int | None = None
        self.killed = False
        self._done = threading.Event()

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("claude", timeout or 0.0)
        assert self.code is not None
        return self.code

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)

    def finish(self, code: int = 0) -> None:
        self.code = code
        self._done.set()


class Rig:
    def __init__(self, tmp_path: Path, *, run_limit_s: float = 60.0) -> None:
        self.folder = tmp_path / "project"
        self.folder.mkdir()
        self.transcript = tmp_path / "s1.jsonl"
        self.transcript.write_text("{}\n", encoding="utf-8")
        os.utime(self.transcript, (NOW - 600, NOW - 600))  # closed ten minutes ago
        self.live: dict[str, LiveSession] = {}
        self.files: dict[str, Path] = {"s1": self.transcript}
        self.cli: str | None = "claude"
        self.spawned: list[tuple[list[str], Path, dict[str, str], str]] = []
        self.processes: list[FakeProcess] = []
        self.spawn_error: OSError | None = None
        self.resumer = CliResumer(
            permission_mode=lambda: "acceptEdits",
            sessions=lambda: dict(self.live),
            find_file=self.files.get,
            find_cli_fn=lambda: self.cli,
            spawn=self.spawn,
            environ={"PATH": "x", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop", "HOME": "h"},
            clock=lambda: NOW,
            run_limit_s=run_limit_s,
        )

    def spawn(self, command: list[str], cwd: Path, env: dict[str, str], prompt: str) -> FakeProcess:
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawned.append((command, cwd, env, prompt))
        process = FakeProcess()
        self.processes.append(process)
        return process


def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout")


def test_a_closed_session_is_resumed_in_its_folder_with_the_prompt_on_stdin(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert not rig.resumer.in_use("s1")
    rig.resumer.send("s1", str(rig.folder), "  run the tests  ")
    [(command, cwd, env, prompt)] = rig.spawned
    assert command == ["claude", "--resume", "s1", "-p", "--output-format", "json", "--permission-mode", "acceptEdits"]
    assert "run the tests" not in " ".join(command)  # never on the command line
    assert cwd == rig.folder
    assert prompt == PROMPT_HEAD + "run the tests"
    assert env == {"PATH": "x", "HOME": "h"}  # the Desktop's own variables are dropped
    assert rig.resumer.in_use("s1")  # our own resume is running: a second one waits
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send("s1", str(rig.folder), "again")
    assert caught.value.code == "in_use"
    rig.processes[0].finish()
    _until(lambda: not rig.resumer.in_use("s1"))


def test_a_session_in_the_live_registry_is_never_written_to(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    statuses: tuple[SessionStatus, ...] = ("busy", "idle", "unknown")
    for status in statuses:
        rig.live = {"s1": LiveSession("s1", 42, status)}
        assert rig.resumer.in_use("s1")
        with pytest.raises(ResumeError) as caught:
            rig.resumer.send("s1", str(rig.folder), "hello")
        assert caught.value.code == "in_use"
    assert rig.spawned == []


def test_a_recently_written_or_missing_transcript_counts_as_in_use(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    os.utime(rig.transcript, (NOW - 30, NOW - 30))  # a client that does not register may have it open
    assert rig.resumer.in_use("s1")
    os.utime(rig.transcript, (NOW - 600, NOW - 600))
    assert not rig.resumer.in_use("s1")
    rig.files.clear()
    assert rig.resumer.in_use("s1")  # nothing to resume: never guess
    with pytest.raises(ResumeError):
        rig.resumer.send("s1", str(rig.folder), "hello")
    assert rig.spawned == []


def test_a_missing_folder_or_command_or_an_empty_text_is_an_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send("s1", str(tmp_path / "gone"), "hello")
    assert caught.value.code == "no_folder"
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send("s1", str(rig.folder), "   ")
    assert caught.value.code == "invalid"
    rig.cli = None
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send("s1", str(rig.folder), "hello")
    assert caught.value.code == "no_cli"
    rig.cli = "claude"
    rig.spawn_error = OSError("denied")
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send("s1", str(rig.folder), "hello")
    assert caught.value.code == "start_failed"
    assert not rig.resumer.in_use("s1")


def test_a_resume_that_runs_too_long_is_stopped(tmp_path: Path) -> None:
    rig = Rig(tmp_path, run_limit_s=0.05)
    rig.resumer.send("s1", str(rig.folder), "hello")
    _until(lambda: rig.processes[0].killed)
    _until(lambda: not rig.resumer.in_use("s1"))


def test_an_unknown_permission_mode_is_left_out() -> None:
    assert resume_command("claude", "s1", "whatever") == ["claude", "--resume", "s1", "-p", "--output-format", "json"]
