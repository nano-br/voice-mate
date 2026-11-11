"""Writing to a terminal, IDE or Remote Control session: only a closed one, resumed in print mode."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.companion.assistant.cli_resume import (
    PROMPT_HEAD,
    TEXT_LIMIT,
    CliResumer,
    ResumeError,
    resume_command,
)
from app.companion.assistant.session_reader import LiveSession, SessionStatus

NOW = 1_800_000_000.0
SID = "0b7f3a52-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


class FakeProcess:
    """A started CLI: it runs until `finish` (or `kill`); `communicate` gets the prompt."""

    def __init__(self) -> None:
        self.code: int | None = None
        self.killed = False
        self.prompt: bytes | None = None
        self.stderr = b""
        self._done = threading.Event()

    def poll(self) -> int | None:
        return self.code

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]:
        if input is not None:
            self.prompt = input
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("claude", timeout or 0.0)
        return None, self.stderr

    def kill(self) -> None:
        self.killed = True
        self.finish(-9)

    def finish(self, code: int = 0, stderr: bytes = b"") -> None:
        self.stderr = stderr
        self.code = code
        self._done.set()


class Rig:
    def __init__(self, tmp_path: Path, *, mode: str = "", run_limit_s: float = 60.0) -> None:
        self.now = NOW
        self.folder = tmp_path / "project"
        self.folder.mkdir()
        self.transcript = tmp_path / f"{SID}.jsonl"
        self.transcript.write_text("{}\n", encoding="utf-8")
        self.touch(600)  # closed ten minutes ago
        self.live: dict[str, LiveSession] = {}
        self.files: dict[str, Path] = {SID: self.transcript}
        self.cli: str | None = "claude"
        self.spawned: list[tuple[list[str], Path, dict[str, str]]] = []
        self.processes: list[FakeProcess] = []
        self.spawn_error: OSError | None = None
        self.failed: list[tuple[str, str]] = []
        self.resumer = CliResumer(
            permission_mode=lambda: mode,
            on_failed=lambda cid, why: self.failed.append((cid, why)),
            sessions=lambda: dict(self.live),
            find_file=self.files.get,
            find_cli_fn=lambda: self.cli,
            spawn=self.spawn,
            environ={"PATH": "x", "CLAUDE_CODE_ENTRYPOINT": "claude-desktop", "HOME": "h"},
            clock=lambda: self.now,
            run_limit_s=run_limit_s,
        )

    def touch(self, seconds_ago: float) -> None:
        os.utime(self.transcript, (self.now - seconds_ago, self.now - seconds_ago))

    def spawn(self, command: list[str], cwd: Path, env: dict[str, str]) -> FakeProcess:
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawned.append((command, cwd, env))
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
    assert rig.resumer.busy(SID) is None
    rig.resumer.send(SID, str(rig.folder), "  run the tests  ")
    [(command, cwd, env)] = rig.spawned
    assert command == ["claude", "--resume", SID, "-p", "--output-format", "json"]  # no permission flag
    assert cwd == rig.folder
    assert env == {"PATH": "x", "HOME": "h"}  # the Desktop's own variables are dropped
    _until(lambda: rig.processes[0].prompt is not None)
    assert rig.processes[0].prompt == (PROMPT_HEAD + "run the tests").encode("utf-8")
    assert rig.resumer.busy(SID) == "running"  # our own resume is running: a second one waits
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send(SID, str(rig.folder), "again")
    assert caught.value.code == "busy"
    rig.processes[0].finish()
    _until(lambda: rig.resumer.busy(SID) is None)
    assert rig.failed == []


def test_right_after_our_resume_ends_the_session_just_answered_and_is_not_open_elsewhere(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.resumer.send(SID, str(rig.folder), "hello")
    rig.processes[0].finish()
    rig.touch(5)  # the resume wrote its turn a moment ago
    _until(lambda: rig.resumer.busy(SID) == "just_answered")
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send(SID, str(rig.folder), "again")
    assert caught.value.code == "busy" and "try again in a moment" in str(caught.value)
    assert "another program" not in str(caught.value)
    rig.now += 600
    assert rig.resumer.busy(SID) is None


@pytest.mark.parametrize(
    ("chosen", "flag"),
    [("", None), ("acceptEdits", "acceptEdits"), ("plan", "plan"), ("default", "default"), ("bypassPermissions", None)],
)
def test_the_permission_flag_appears_only_for_a_mode_the_person_chose_and_never_bypass(
    tmp_path: Path, chosen: str, flag: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="app.companion.assistant.cli_resume")
    rig = Rig(tmp_path, mode=chosen)
    rig.resumer.send(SID, str(rig.folder), "hello")
    command = rig.spawned[0][0]
    if flag is None:
        assert "--permission-mode" not in command
    else:
        assert command[-2:] == ["--permission-mode", flag]
    assert ("bypassPermissions is refused" in caplog.text) == (chosen == "bypassPermissions")
    rig.processes[0].finish()


def test_an_error_exit_is_reported_with_the_last_error_line_in_the_log_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.companion.assistant.cli_resume")
    rig = Rig(tmp_path)
    rig.resumer.send(SID, str(rig.folder), "secret plan")
    rig.processes[0].finish(1, b"starting\nError: No conversation found\n")
    _until(lambda: bool(rig.failed))
    assert rig.failed == [(SID, "Claude Code ended with exit code 1")]
    assert "Error: No conversation found" in caplog.text and "starting" not in caplog.text
    assert "secret plan" not in caplog.text


def test_a_resume_that_runs_too_long_is_stopped_and_reported(tmp_path: Path) -> None:
    rig = Rig(tmp_path, run_limit_s=0.05)
    rig.resumer.send(SID, str(rig.folder), "hello")
    _until(lambda: bool(rig.failed))
    assert rig.processes[0].killed
    assert "was stopped" in rig.failed[0][1]
    _until(lambda: rig.resumer.busy(SID) is None)


def test_a_session_in_the_live_registry_is_never_written_to(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    statuses: tuple[SessionStatus, ...] = ("busy", "idle", "unknown")
    for status in statuses:
        rig.live = {SID: LiveSession(SID, 42, status)}
        assert rig.resumer.busy(SID) == "open"
        with pytest.raises(ResumeError) as caught:
            rig.resumer.send(SID, str(rig.folder), "hello")
        assert caught.value.code == "in_use"
    assert rig.spawned == []


def test_a_recently_written_or_missing_transcript_counts_as_open(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.touch(30)  # a client that does not register may have it open
    assert rig.resumer.busy(SID) == "open"
    rig.touch(600)
    assert rig.resumer.busy(SID) is None
    rig.files.clear()
    assert rig.resumer.busy(SID) == "open"  # nothing to resume: never guess
    with pytest.raises(ResumeError):
        rig.resumer.send(SID, str(rig.folder), "hello")
    assert rig.spawned == []


@pytest.mark.parametrize(
    "bad", ["s1", "../" + SID, SID.upper(), SID + "\n", SID + " --dangerously-skip-permissions", ""]
)
def test_a_session_id_that_is_not_a_uuid_is_refused_before_any_command(tmp_path: Path, bad: str) -> None:
    rig = Rig(tmp_path)
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send(bad, str(rig.folder), "hello")
    assert caught.value.code == "invalid" and "session id" in str(caught.value)
    assert rig.spawned == []


def test_a_missing_folder_or_command_an_empty_or_too_long_text_is_an_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    cases: list[tuple[str, str, str]] = [
        (str(tmp_path / "gone"), "hello", "no_folder"),
        (str(rig.folder), "   ", "invalid"),
        (str(rig.folder), "x" * (TEXT_LIMIT + 1), "too_long"),
    ]
    for folder, text, code in cases:
        with pytest.raises(ResumeError) as caught:
            rig.resumer.send(SID, folder, text)
        assert caught.value.code == code
    rig.cli = None
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send(SID, str(rig.folder), "hello")
    assert caught.value.code == "no_cli"
    rig.cli = "claude"
    rig.spawn_error = OSError("denied")
    with pytest.raises(ResumeError) as caught:
        rig.resumer.send(SID, str(rig.folder), "hello")
    assert caught.value.code == "start_failed"
    assert rig.resumer.busy(SID) is None
    rig.spawn_error = None
    rig.resumer.send(SID, str(rig.folder), "x" * TEXT_LIMIT)  # exactly the limit goes
    rig.processes[0].finish()


def test_an_unknown_permission_mode_is_left_out() -> None:
    assert resume_command("claude", SID, "whatever") == ["claude", "--resume", SID, "-p", "--output-format", "json"]
