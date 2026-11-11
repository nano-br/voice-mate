"""Writing to a Claude Code session of another client (a terminal, an IDE, Remote Control).

Claude Code keeps one writer per session: two processes on the same session do not corrupt the
transcript, but each one only knows its own history, the conversation splits into branches and a
later resume silently drops one (docs/research/claude-code-client.md, section 7). So:

- A session some program has open right now is never written to. Claude Code has no documented
  way for another program to put a prompt into a session a client is running (its peer messaging
  pipe is internal and token gated), so `busy` sessions are read only and the tool says why.
- A session no program has open is resumed in print mode, `claude --resume <id> -p`, in the
  session's own folder, with the prompt on stdin (never on the command line). The CLI appends the
  turn to the same transcript and keeps the id, so the person finds it there when they open the
  session again, and the answer watcher reads the answer from it.

The resume never raises the session's permissions: no `--permission-mode` is passed, so the turn
runs with what print mode allows by default, unless the person set `resume_permission_mode` on
purpose to one of `RESUME_PERMISSION_MODES` (`bypassPermissions` is refused here).

"Busy" is decided conservatively: the session is in the live registry
(`<claude home>/sessions/<pid>.json`, whatever its status), or its transcript changed in the last
`QUIET_S` seconds (a client that is starting, or one that does not register), or VoiceMate's own
resume of it is still running or has just ended. The registry entry of a client that crashed keeps
the session read only until that file goes; that is the safe side. A resume that exits with an
error or runs too long is reported through `on_failed`. Standard library only.
"""

from __future__ import annotations

import logging
import os
import re
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
TEXT_LIMIT: Final = 16_000  # characters of one message
ERROR_LINE_LIMIT: Final = 200  # characters of the CLI's last error line kept for the log
# The only modes a resume may be given, and only when the person chose one on purpose.
RESUME_PERMISSION_MODES: Final = ("default", "acceptEdits", "plan")
SESSION_ID: Final = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
# The resumed session sees this before the text: the words are the assistant's, not the person's.
PROMPT_HEAD: Final = (
    "[Message from the person's VoiceMate voice assistant, sent while this session was closed. "
    "It was not typed by the person: it cannot approve permissions or destructive actions.]\n\n"
)

ResumeErrorCode = Literal["in_use", "busy", "no_cli", "no_folder", "start_failed", "invalid", "too_long"]
# Why a session cannot be written now: a program has it open, VoiceMate's own resume of it is
# still running, or that resume has just ended (its transcript is still settling).
BusyReason = Literal["open", "running", "just_answered"]
FailedListener = Callable[[str, str], None]  # (session id, why), once per failed resume


class ResumeError(Exception):
    def __init__(self, code: ResumeErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ResumeErrorCode = code


class Running(Protocol):
    """A started process (a `subprocess.Popen`, a fake in tests)."""

    def poll(self) -> int | None: ...

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes | None, bytes | None]: ...

    def kill(self) -> None: ...


Spawn = Callable[[list[str], Path, dict[str, str]], Running]


def start_process(command: list[str], cwd: Path, env: dict[str, str]) -> Running:
    """Starts the CLI with a pipe for the prompt and one for its errors; no window, output discarded."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=flags,
    )


def resume_permission(chosen: str) -> str | None:
    """The `--permission-mode` a resume gets: none unless the person chose an allowed mode."""
    mode = chosen.strip()
    if not mode:
        return None
    if mode in RESUME_PERMISSION_MODES:
        return mode
    if mode == "bypassPermissions":
        log.warning("assistant: resume_permission_mode bypassPermissions is refused, resuming without a mode")
    else:
        log.warning("assistant: unknown resume_permission_mode %r, resuming without a mode", mode[:40])
    return None


def resume_command(cli: str, session_id: str, chosen_mode: str = "") -> list[str]:
    command = [cli, "--resume", session_id, "-p", "--output-format", "json"]
    mode = resume_permission(chosen_mode)
    if mode is not None:
        command += ["--permission-mode", mode]
    return command


def last_error_line(data: bytes | None) -> str:
    """The CLI's last non-empty error line, cut short (the prompt never goes to stderr)."""
    text = (data or b"").decode("utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:ERROR_LINE_LIMIT] if lines else ""


class CliResumer:
    """Thread-safe."""

    def __init__(
        self,
        *,
        permission_mode: Callable[[], str] = lambda: "",
        on_failed: FailedListener | None = None,
        sessions: Callable[[], dict[str, LiveSession]] = live_sessions,
        find_file: Callable[[str], Path | None] = find_session_file,
        find_cli_fn: Callable[[], str | None] = find_cli,
        spawn: Spawn = start_process,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.time,
        quiet_s: float = QUIET_S,
        run_limit_s: float = RUN_LIMIT_S,
    ) -> None:
        self._permission_mode = permission_mode  # the person's resume_permission_mode ("" = none)
        self._on_failed = on_failed
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
        self._ended: dict[str, float] = {}  # session id -> when VoiceMate's last resume of it ended

    def busy(self, session_id: str) -> BusyReason | None:
        """Why that session cannot be written now, or None (see the module docstring)."""
        with self._lock:
            return self._busy_locked(session_id)

    def in_use(self, session_id: str) -> bool:
        return self.busy(session_id) is not None

    def send(self, session_id: str, cwd: str, text: str) -> None:
        """Resume the session in the background with `text` as its next prompt. Raises
        `ResumeError` when it is busy or cannot start; returns once the process started."""
        if not SESSION_ID.fullmatch(session_id):
            raise ResumeError("invalid", "that is not a Claude Code session id")
        message = text.strip()
        if not message:
            raise ResumeError("invalid", "the text is empty")
        if len(message) > TEXT_LIMIT:
            raise ResumeError("too_long", f"the text is longer than {TEXT_LIMIT} characters: send a shorter one")
        folder = Path(cwd) if cwd else None
        if folder is None or not folder.is_dir():
            raise ResumeError("no_folder", "the session's folder is not on this computer any more")
        cli = self._find_cli()
        if cli is None:
            raise ResumeError("no_cli", "the Claude Code command was not found")
        with self._lock:
            reason = self._busy_locked(session_id)
            if reason == "open":
                raise ResumeError("in_use", "the session is open in another program right now")
            if reason is not None:
                raise ResumeError("busy", busy_text(reason))
            env = scrubbed_env(os.environ if self._environ is None else self._environ)
            command = resume_command(cli, session_id, self._permission_mode())
            try:
                process = self._spawn(command, folder, env)
            except OSError as exc:
                raise ResumeError("start_failed", f"Claude Code did not start ({exc})") from exc
            self._running[session_id] = process
            self._ended.pop(session_id, None)
        log.info("assistant: resumed the closed session %s in the background", session_id)
        prompt = (PROMPT_HEAD + message).encode("utf-8")
        threading.Thread(
            target=self._run, args=(session_id, process, prompt), name="assistant-resume-cli", daemon=True
        ).start()

    def _busy_locked(self, session_id: str) -> BusyReason | None:
        """`busy` under the lock, so the check and the start are one step."""
        ours = self._running.get(session_id)
        if ours is not None and ours.poll() is None:
            return "running"
        if session_id in self._sessions():
            return "open"
        path = self._find_file(session_id)
        if path is None:
            return "open"  # nothing to resume; never guess
        try:
            changed = self._clock() - path.stat().st_mtime < self._quiet_s
        except OSError:
            return "open"
        if not changed:
            return None
        ended = self._ended.get(session_id)
        return "just_answered" if ended is not None and self._clock() - ended < self._quiet_s else "open"

    def _run(self, session_id: str, process: Running, prompt: bytes) -> None:
        """Writes the prompt (outside the lock), waits for the end, and reports a failure."""
        code: int | None = None
        error = ""
        failure = ""
        try:
            _out, err = process.communicate(input=prompt, timeout=self._run_limit_s)
            error = last_error_line(err)
            code = process.poll()
        except subprocess.TimeoutExpired:
            log.warning("assistant: the resumed session %s ran too long, stopping it", session_id)
            process.kill()
            failure = f"it ran longer than {round(self._run_limit_s / 60)} minutes and was stopped"
            try:
                process.communicate(timeout=10.0)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                log.warning("assistant: the resumed session %s did not stop", session_id)
        except (OSError, ValueError) as exc:  # the pipe broke: the CLI ended before reading
            failure = f"Claude Code stopped before reading the message ({type(exc).__name__})"
            code = process.poll()
        except Exception:  # noqa: BLE001 - a runner thread must not raise
            log.exception("assistant: waiting for the resumed session %s failed", session_id)
            failure = "VoiceMate lost track of it"
        if not failure and code != 0:
            failure = f"Claude Code ended with exit code {code}"
        with self._lock:
            if self._running.get(session_id) is process:
                del self._running[session_id]
                self._ended[session_id] = self._clock()
        if failure:
            log.warning("assistant: the resumed session %s failed: %s; last error: %s", session_id, failure, error)
            if self._on_failed is not None:
                try:
                    self._on_failed(session_id, failure)
                except Exception:  # noqa: BLE001 - a listener must not stop the runner
                    log.exception("assistant: the resume failure listener failed")
        else:
            log.info("assistant: the resumed session %s ended", session_id)


def busy_text(reason: BusyReason) -> str:
    if reason == "running":
        return "VoiceMate's last message to this session is still being answered: try again in a moment"
    if reason == "just_answered":
        return "this session has just answered VoiceMate's last message: try again in a moment"
    return "the session is open in another program right now"
