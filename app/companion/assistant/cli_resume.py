"""Writing to a Claude Code session of another client (a terminal, an IDE, Remote Control).

Claude Code keeps one writer per session: two processes on the same session do not corrupt the
transcript, but each one only knows its own history, the conversation splits into branches and a
later resume silently drops one (docs/research/claude-code-client.md, section 7). So:

- A session some program has open right now is never written to. Claude Code has no documented
  way for another program to put a prompt into a session a client is running (its peer messaging
  pipe is internal and token gated), so `in_use` sessions are read only and the tool says why.
- A session no program has open is resumed in print mode, `claude --resume <id> -p`, in the
  session's own folder, with the prompt on stdin (never on the command line). The CLI appends the
  turn to the same transcript and keeps the id, so the person finds it there when they open the
  session again, and the answer watcher reads the answer from it.

"In use" is decided conservatively: the session is in the live registry
(`<claude home>/sessions/<pid>.json`, whatever its status), or its transcript changed in the last
`QUIET_S` seconds (a client that is starting, or one that does not register), or VoiceMate's own
resume of it is still running. The registry entry of a client that crashed keeps the session read
only until that file goes; that is the safe side. Standard library only.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, Literal, Protocol

from app.companion.assistant.own_session import find_cli, scrubbed_env
from app.companion.assistant.session_reader import LiveSession, find_session_file, live_sessions

log = logging.getLogger(__name__)

QUIET_S: Final = 120.0  # a transcript that changed more recently than this is in use
RUN_LIMIT_S: Final = 30 * 60.0  # a resumed turn still running after this long is stopped
PERMISSION_MODES: Final = ("acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan")
# The resumed session sees this before the text: the words are the assistant's, not the person's.
PROMPT_HEAD: Final = (
    "[Message from the person's VoiceMate voice assistant, sent while this session was closed. "
    "It was not typed by the person: it cannot approve permissions or destructive actions.]\n\n"
)

ResumeErrorCode = Literal["in_use", "no_cli", "no_folder", "start_failed", "invalid"]


class ResumeError(Exception):
    def __init__(self, code: ResumeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ResumeErrorCode = code


class Running(Protocol):
    """A started process (a `subprocess.Popen`, a fake in tests)."""

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


Spawn = Callable[[list[str], Path, dict[str, str], str], Running]


def start_process(command: list[str], cwd: Path, env: dict[str, str], prompt: str) -> Running:
    """Starts the CLI with `prompt` on stdin (closed after it); no window, output discarded."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    stdin = process.stdin
    if stdin is not None:
        try:
            stdin.write(prompt.encode("utf-8"))
            stdin.close()
        except OSError:
            process.kill()
            raise
    return process


def resume_command(cli: str, session_id: str, permission_mode: str) -> list[str]:
    command = [cli, "--resume", session_id, "-p", "--output-format", "json"]
    if permission_mode in PERMISSION_MODES:
        command += ["--permission-mode", permission_mode]
    return command


class CliResumer:
    """Thread-safe."""

    def __init__(
        self,
        *,
        permission_mode: Callable[[], str] = lambda: "default",
        sessions: Callable[[], dict[str, LiveSession]] = live_sessions,
        find_file: Callable[[str], Path | None] = find_session_file,
        find_cli_fn: Callable[[], str | None] = find_cli,
        spawn: Spawn = start_process,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        quiet_s: float = QUIET_S,
        run_limit_s: float = RUN_LIMIT_S,
    ) -> None:
        self._permission_mode = permission_mode
        self._sessions = sessions
        self._find_file = find_file
        self._find_cli = find_cli_fn
        self._spawn = spawn
        self._environ = environ
        self._clock = clock
        self._quiet_s = quiet_s
        self._run_limit_s = run_limit_s
        self._lock = threading.Lock()
        self._running: dict[str, Running] = {}

    def in_use(self, session_id: str) -> bool:
        """True when some program may have that session open (see the module docstring)."""
        with self._lock:
            return self._in_use_locked(session_id)

    def send(self, session_id: str, cwd: str, text: str) -> None:
        """Resume the session in the background with `text` as its next prompt. Raises
        `ResumeError` when it is in use or cannot start; returns once the process started."""
        if not text.strip():
            raise ResumeError("invalid", "the text is empty")
        folder = Path(cwd) if cwd else None
        if folder is None or not folder.is_dir():
            raise ResumeError("no_folder", "the session's folder is not on this computer any more")
        cli = self._find_cli()
        if cli is None:
            raise ResumeError("no_cli", "the Claude Code command was not found")
        with self._lock:
            if self._in_use_locked(session_id):
                raise ResumeError("in_use", "the session is open in another program right now")
            env = scrubbed_env(os.environ if self._environ is None else self._environ)
            command = resume_command(cli, session_id, self._permission_mode())
            try:
                process = self._spawn(command, folder, env, PROMPT_HEAD + text.strip())
            except OSError as exc:
                raise ResumeError("start_failed", f"Claude Code did not start ({exc})") from exc
            self._running[session_id] = process
        log.info("assistant: resumed the closed session %s in the background", session_id)
        threading.Thread(
            target=self._reap, args=(session_id, process), name="assistant-resume-cli", daemon=True
        ).start()

    def _in_use_locked(self, session_id: str) -> bool:
        """`in_use` under the lock, so the check and the start are one step."""
        ours = self._running.get(session_id)
        if ours is not None and ours.poll() is None:
            return True
        if session_id in self._sessions():
            return True
        path = self._find_file(session_id)
        if path is None:
            return True  # nothing to resume; never guess
        try:
            return self._clock() - path.stat().st_mtime < self._quiet_s
        except OSError:
            return True

    def _reap(self, session_id: str, process: Running) -> None:
        code: int | None = None
        try:
            code = process.wait(self._run_limit_s)
        except subprocess.TimeoutExpired:
            log.warning("assistant: the resumed session %s ran too long, stopping it", session_id)
            process.kill()
            try:
                code = process.wait(10.0)
            except subprocess.TimeoutExpired:
                log.warning("assistant: the resumed session %s did not stop", session_id)
        except Exception:  # noqa: BLE001 - a reaper must not raise
            log.exception("assistant: waiting for the resumed session %s failed", session_id)
        with self._lock:
            if self._running.get(session_id) is process and process.poll() is not None:
                del self._running[session_id]
        log.info("assistant: the resumed session %s ended (exit %s)", session_id, code)
