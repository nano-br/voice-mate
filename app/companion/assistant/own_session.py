"""The assistant's own Claude Code conversation: one persistent `stream-json` child.

VoiceMate starts `claude -p --input-format stream-json --output-format stream-json` in a stable
working directory, sends `/voicemate-assistant` as the first prompt, then the undocumented
`remote_control` control request that makes the session show in the Claude Desktop and on the
phone (docs/research/claude-code-client.md, sections 2 and 6.1). VoiceMate is the only writer of
that session: a restart resumes it by id only when no live process owns it (report, section 7).

The Desktop's host variables are dropped from the child's environment (report, section 2): a
child that inherits them rewrote the credentials file in the lab. The reader thread consumes
stdout (the session id, the Remote Control answer, errors); the replies themselves are read from
the transcript on disk by the service. Standard library only (no Qt).

The same port runs the conversations the assistant opens itself (conversations.py): another
first prompt, name, model and working directory, and no MCP server. Every child gets the
`initialize` control request first; it declares the host's "sdk" MCP servers (mcp_server.py),
whose JSON-RPC messages then arrive as `mcp_message` control requests on stdout and are answered
on stdin (tested on the real CLI, 2026-10-28). Their tools are allowed with `--allowedTools`,
since a stream-json session without a permission prompt tool refuses every MCP tool otherwise.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Protocol

from app.companion.assistant.contract import PortListener, PortStatus
from app.companion.assistant.mcp_server import McpServer
from app.companion.assistant.session_reader import LiveSession, find_session_file, live_sessions
from app.companion.contract import AssistantEffort, AssistantModel, AssistantPermission, AssistantSettings

log = logging.getLogger(__name__)

CLI_ENV_VAR: Final = "VOICEMATE_CLAUDE_CLI"
SESSION_NAME: Final = "VoiceMate"
FIRST_PROMPT: Final = "/voicemate-assistant"
REMOTE_CONTROL_REQUEST_ID: Final = "voicemate-remote-control"
INITIALIZE_REQUEST_ID: Final = "voicemate-initialize"
SET_MODEL_REQUEST_PREFIX: Final = "voicemate-set-model-"
STOP_WAIT_S: Final = 4.0
KILL_WAIT_S: Final = 2.0
TAIL_LINES: Final = 8  # last non-JSON output lines kept for the log when the child dies
# The CLI aliases of the three models (the CLI picks the current version of each).
MODEL_ALIASES: Final[dict[AssistantModel, str]] = {"haiku": "haiku", "sonnet": "sonnet", "opus": "opus"}
# Settings the person chose, not Desktop host state (same rule as the lab's scrub_host_env).
_KEEP_ENV: Final = frozenset({"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH"})
_HOST_ENV_NAMES: Final = frozenset({"CLAUDECODE", "CLAUDE_PID"})
_LOGIN_HINTS: Final = ("/login", "not logged in", "invalid api key", "authentication")


def scrubbed_env(environ: Mapping[str, str]) -> dict[str, str]:
    """`environ` without the variables the Claude Desktop sets on its own sessions."""
    return {
        name: value
        for name, value in environ.items()
        if name in _KEEP_ENV or not (name.startswith("CLAUDE_CODE_") or name in _HOST_ENV_NAMES)
    }


def install_locations(environ: Mapping[str, str]) -> list[Path]:
    """Where the Claude Code installers put the command, for when it is not on PATH.

    The native installer writes `~/.local/bin/claude(.exe)` and npm writes `%APPDATA%/npm/claude.cmd`,
    but neither folder is always on the PATH a program started at sign-in gets: a shell profile
    may be what adds it, so a terminal finds `claude` and VoiceMate does not."""
    found: list[Path] = []
    home = environ.get("USERPROFILE") or environ.get("HOME")
    if home:
        local_bin = Path(home) / ".local" / "bin"
        found.append(local_bin / "claude.exe")
        if os.name != "nt":
            found.append(local_bin / "claude")  # Windows cannot start it without an extension
    appdata = environ.get("APPDATA")
    if appdata:
        found.append(Path(appdata) / "npm" / "claude.cmd")
    return found


def find_cli(environ: Mapping[str, str] | None = None, which: Callable[[str], str | None] = shutil.which) -> str | None:
    """`$VOICEMATE_CLAUDE_CLI` when set (a path or a command name), else `claude` on PATH, else
    the installers' own folders (`install_locations`)."""
    env = os.environ if environ is None else environ
    override = env.get(CLI_ENV_VAR, "").strip()
    if override:
        return which(override) or (override if Path(override).is_file() else None)
    on_path = which("claude")
    if on_path:
        return on_path
    return next((str(path) for path in install_locations(env) if path.is_file()), None)


def build_command(
    cli: str,
    settings: AssistantSettings,
    resume: str = "",
    *,
    model: str | None = None,
    effort: AssistantEffort | None = None,
    name: str = SESSION_NAME,
    permission_mode: AssistantPermission | None = None,
    session_id: str = "",
    mcp_servers: Iterable[str] = (),
) -> list[str]:
    """The CLI command line. `model` and `effort` override the settings' (an alias or a full
    model id); `session_id` fixes the id of a new session; `mcp_servers` are the names of the
    host's sdk servers, whose tools are allowed without a prompt."""
    command = [
        cli,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model or MODEL_ALIASES[settings.model],
        "--effort",
        effort or settings.effort,
        "--name",
        name,
    ]
    if permission_mode is not None:
        command += ["--permission-mode", permission_mode]
    servers = list(mcp_servers)
    if servers:
        command += ["--allowedTools", ",".join(f"mcp__{server}" for server in servers)]
    if resume:
        command += ["--resume", resume]
    elif session_id:
        command += ["--session-id", session_id]
    return command


# --- the child process ---------------------------------------------------------------------


class Child(Protocol):
    """The part of a process the port uses (a fake in the tests)."""

    def write_line(self, line: str) -> None:
        """Raises OSError when the process is gone."""

    def read_line(self) -> str | None:
        """Blocks; None at end of output."""

    def close_stdin(self) -> None: ...

    def wait(self, timeout: float) -> int | None:
        """The exit code, or None when it did not end in time."""

    def kill(self) -> None: ...


class PopenChild:
    def __init__(self, command: list[str], cwd: Path, env: dict[str, str]) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )

    def write_line(self, line: str) -> None:
        stdin = self._proc.stdin
        if stdin is None:
            raise OSError("stdin is closed")
        stdin.write(line + "\n")
        stdin.flush()

    def read_line(self) -> str | None:
        stdout = self._proc.stdout
        if stdout is None:
            return None
        line = stdout.readline()
        return line if line else None

    def close_stdin(self) -> None:
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass

    def wait(self, timeout: float) -> int | None:
        try:
            return self._proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> None:
        try:
            self._proc.kill()
        except OSError:
            pass


Spawn = Callable[[list[str], Path, dict[str, str]], Child]


# --- which session is ours -----------------------------------------------------------------


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    bridge_session_id: str
    cwd: str


class SessionStore(Protocol):
    """Where the port keeps which session is its own."""

    def load(self) -> SessionRecord | None: ...

    def save(self, record: SessionRecord) -> None: ...

    def clear(self) -> None: ...


class SessionStateStore:
    """`{session_id, bridge_session_id, cwd}` in a small JSON file; never raises on a bad file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> SessionRecord | None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        session_id, bridge, cwd = raw.get("session_id"), raw.get("bridge_session_id", ""), raw.get("cwd")
        if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str):
            return None
        return SessionRecord(session_id, bridge if isinstance(bridge, str) else "", cwd)

    def save(self, record: SessionRecord) -> None:
        payload = json.dumps(
            {"session_id": record.session_id, "bridge_session_id": record.bridge_session_id, "cwd": record.cwd},
            indent=2,
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".assistant-session.", suffix=".tmp")
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                    file.write(payload + "\n")
                tmp.replace(self._path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        except OSError as exc:
            log.warning("assistant: cannot save %s (%s)", self._path, exc)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)


def _find_value(obj: object, key: str, depth: int = 0) -> str:
    """The first string under `key` anywhere in a nested JSON value ("" when absent)."""
    if depth > 6:
        return ""
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
        for child in obj.values():
            found = _find_value(child, key, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for child in obj:
            found = _find_value(child, key, depth + 1)
            if found:
                return found
    return ""


class OwnSessionPort:
    """Implements `ConversationPort` on a persistent `claude` child.

    `first_prompt` None starts without a prompt (a resumed conversation that waits for the next
    one): the port is ready once the CLI answers `initialize`, and Remote Control is requested
    right then instead of after the first turn. `new_session_id` gives a new session its id up
    front (`--session-id`), so the status carries it before the CLI reports it.
    """

    def __init__(
        self,
        settings: AssistantSettings,
        *,
        cwd: Path,
        state_path: Path | None = None,
        store: SessionStore | None = None,
        spawn: Spawn = PopenChild,
        find_cli_fn: Callable[[], str | None] = find_cli,
        environ: Mapping[str, str] | None = None,
        sessions: Callable[[], dict[str, LiveSession]] = live_sessions,
        transcript_exists: Callable[[str], bool] = lambda sid: find_session_file(sid) is not None,
        first_prompt: str | None = FIRST_PROMPT,
        session_name: str = SESSION_NAME,
        model: str | None = None,
        effort: AssistantEffort | None = None,
        permission_mode: AssistantPermission | None = None,
        mcp_servers: Iterable[McpServer] = (),
        new_session_id: Callable[[], str] | None = None,
    ) -> None:
        if store is None:
            if state_path is None:
                raise ValueError("OwnSessionPort needs a state_path or a store")
            store = SessionStateStore(state_path)
        self._settings = settings
        self._cwd = cwd
        self._store = store
        self._first_prompt = first_prompt
        self._session_name = session_name
        self._model = model
        self._effort = effort
        self._permission_mode = permission_mode
        self._mcp = {server.name: server for server in mcp_servers}
        self._new_session_id = new_session_id
        self._write_lock = threading.Lock()
        self._request_counter = 0
        self._spawn = spawn
        self._find_cli = find_cli_fn
        self._environ = environ
        self._sessions = sessions
        self._transcript_exists = transcript_exists
        self._lock = threading.RLock()
        self._status = PortStatus()
        self._listener: PortListener | None = None
        self._child: Child | None = None
        self._reader: threading.Thread | None = None
        self._stopping = False
        self._pending_turns = 0
        self._record: SessionRecord | None = None
        self._remote_requested = False
        self._tail: deque[str] = deque(maxlen=TAIL_LINES)

    # --- ConversationPort --------------------------------------------------------------------

    def status(self) -> PortStatus:
        with self._lock:
            return self._status

    def start(self, on_change: PortListener) -> None:
        with self._lock:
            if self._child is not None:
                return
            self._listener = on_change
            self._stopping = False
            self._pending_turns = 0
            self._tail.clear()
        cli = self._find_cli()
        if cli is None:
            self._set(PortStatus(state="error", error="cli_missing", detail="the claude command was not found"))
            return
        resume = self._resumable_id()
        new_id = "" if resume or self._new_session_id is None else self._new_session_id()
        command = build_command(
            cli,
            self._settings,
            resume,
            model=self._model,
            effort=self._effort,
            name=self._session_name,
            permission_mode=self._permission_mode,
            session_id=new_id,
            mcp_servers=self._mcp,
        )
        try:
            self._cwd.mkdir(parents=True, exist_ok=True)
            environ = os.environ if self._environ is None else self._environ
            child = self._spawn(command, self._cwd, scrubbed_env(environ))
        except OSError as exc:
            log.warning("assistant: cannot start %s: %s", cli, exc)
            self._set(PortStatus(state="error", error="start_failed", detail=str(exc)))
            return
        log.info("assistant: started %s (%s)", cli, f"resuming {resume}" if resume else "new session")
        session_id = resume or new_id
        with self._lock:
            self._child = child
            self._record = SessionRecord(session_id, "", str(self._cwd)) if session_id else None
            self._pending_turns = 1 if self._first_prompt is not None else 0
            self._remote_requested = False
            self._status = PortStatus(state="starting", session_id=session_id, remote="pending")
            reader = threading.Thread(target=self._read_loop, args=(child,), name="assistant-reader", daemon=True)
            self._reader = reader
            record = self._record
        if new_id and record is not None:
            self._store.save(record)
        self._notify()
        reader.start()
        initialize: dict[str, object] = {"subtype": "initialize", "hooks": None}
        if self._mcp:
            initialize["sdkMcpServers"] = list(self._mcp)
        self._write({"type": "control_request", "request_id": INITIALIZE_REQUEST_ID, "request": initialize})
        if self._first_prompt is not None:
            self._write({"type": "user", "message": {"role": "user", "content": self._first_prompt}})

    def set_model(self, model: str) -> bool:
        """Switch the model of the running conversation (the `set_model` control request; the
        next turn uses it, and a restart keeps it). False when there is no running conversation."""
        with self._lock:
            if self._child is None:
                return False
            self._request_counter += 1
            request_id = f"{SET_MODEL_REQUEST_PREFIX}{self._request_counter}"
        request = {"subtype": "set_model", "model": model}
        if not self._write({"type": "control_request", "request_id": request_id, "request": request}):
            return False
        with self._lock:
            self._model = model
        return True

    def send(self, text: str) -> bool:
        with self._lock:
            if self._child is None:
                return False
            self._pending_turns += 1
            was_ready = self._status.state == "ready"
            if was_ready:
                self._status = replace(self._status, state="busy")
        if not self._write({"type": "user", "message": {"role": "user", "content": text}}):
            with self._lock:
                self._pending_turns = max(0, self._pending_turns - 1)
                if was_ready and self._pending_turns == 0 and self._status.state == "busy":
                    self._status = replace(self._status, state="ready")
            return False
        self._notify()
        return True

    def stop(self) -> None:
        with self._lock:
            child, reader = self._child, self._reader
            self._stopping = True
        if child is None:
            return
        child.close_stdin()
        if child.wait(STOP_WAIT_S) is None:
            log.info("assistant: the process did not end after stdin closed, killing it")
            child.kill()
            child.wait(KILL_WAIT_S)
        if reader is not None and reader is not threading.current_thread():
            reader.join(KILL_WAIT_S)
        with self._lock:
            self._child = None
            self._reader = None
        self._set(PortStatus(state="stopped", session_id=self._status.session_id))

    # --- internals ---------------------------------------------------------------------------

    def _resumable_id(self) -> str:
        """The stored session id, only when it is safe to resume it: from the same directory,
        its transcript still exists and no live process owns it (one writer per session)."""
        record = self._store.load()
        if record is None or record.cwd != str(self._cwd):
            return ""
        if not self._transcript_exists(record.session_id):
            log.info("assistant: session %s has no transcript, starting a new one", record.session_id)
            return ""
        if record.session_id in self._sessions():
            log.info("assistant: session %s is owned by a live process, starting a new one", record.session_id)
            return ""
        return record.session_id

    def _write(self, message: dict[str, object]) -> bool:
        with self._lock:
            child = self._child
        if child is None:
            return False
        try:
            with self._write_lock:  # the reader and the tool threads write too
                child.write_line(json.dumps(message))
        except OSError as exc:
            log.warning("assistant: cannot write to the process (%s)", exc)
            return False
        return True

    def _set(self, status: PortStatus) -> None:
        with self._lock:
            if status == self._status:
                return
            self._status = status
        self._notify()

    def _notify(self) -> None:
        with self._lock:
            listener, status = self._listener, self._status
        if listener is not None:
            try:
                listener(status)
            except Exception:  # noqa: BLE001 - a listener must not kill the reader thread
                log.exception("assistant: status listener failed")

    def _read_loop(self, child: Child) -> None:
        while (line := child.read_line()) is not None:
            self._handle_line(line.strip())
        code = child.wait(KILL_WAIT_S)
        with self._lock:
            if self._child is not child:
                return
            expected = self._stopping
            if not expected:
                self._child = None
            session_id = self._status.session_id
        if expected:
            return  # stop() reports the end
        detail = " | ".join(self._tail) or f"exit code {code}"
        log.warning("assistant: the process ended by itself (%s)", detail)
        self._set(PortStatus(state="error", error="exited", detail=detail, session_id=session_id))

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            self._tail.append(line[:300])
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            session_id = event.get("session_id")
            if isinstance(session_id, str) and session_id:
                self._on_session_id(session_id)
        elif kind == "control_response":
            self._on_control_response(event)
        elif kind == "control_request":
            self._on_control_request(event)
        elif kind == "result":
            self._on_result(event)
        elif kind == "system" and event.get("subtype") == "bridge_state":
            log.info("assistant: bridge_state %s", event.get("state"))

    def _on_session_id(self, session_id: str) -> None:
        with self._lock:
            bridge = self._record.bridge_session_id if self._record and self._record.session_id == session_id else ""
            self._record = SessionRecord(session_id, bridge, str(self._cwd))
            record = self._record
            self._status = replace(self._status, session_id=session_id)
        self._store.save(record)
        self._notify()

    def _on_control_request(self, event: dict[str, Any]) -> None:
        """A request from the CLI: an MCP message for one of our sdk servers (answered on its own
        thread, a tool may take a few seconds), anything else is refused."""
        request_id = event.get("request_id")
        request = event.get("request")
        if not isinstance(request_id, str) or not isinstance(request, dict):
            return
        if request.get("subtype") != "mcp_message":
            log.info("assistant: refusing control request %s", request.get("subtype"))
            self._respond_error(request_id, f"unsupported control request: {request.get('subtype')}")
            return
        threading.Thread(target=self._answer_mcp, args=(request_id, request), name="assistant-mcp", daemon=True).start()

    def _answer_mcp(self, request_id: str, request: dict[str, Any]) -> None:
        name, message = request.get("server_name"), request.get("message")
        if not isinstance(message, dict):
            self._respond_error(request_id, "missing message for the MCP request")
            return
        server = self._mcp.get(name) if isinstance(name, str) else None
        reply: dict[str, Any] | None
        if server is None:
            reply = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32601, "message": f"no server {name!r}"},
            }
        else:
            try:
                reply = server.handle(message)
            except Exception as exc:  # noqa: BLE001 - always answer, the CLI waits for it
                log.exception("assistant: MCP server %s failed", name)
                reply = {"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32603, "message": str(exc)}}
        # A notification gets no JSON-RPC reply, but the control request still needs an answer.
        mcp_response = reply if reply is not None else {"jsonrpc": "2.0", "result": {}}
        self._write(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request_id,
                    "response": {"mcp_response": mcp_response},
                },
            }
        )

    def _respond_error(self, request_id: str, error: str) -> None:
        self._write(
            {"type": "control_response", "response": {"subtype": "error", "request_id": request_id, "error": error}}
        )

    def _on_initialized(self, event: dict[str, object]) -> None:
        response = event.get("response")
        if isinstance(response, dict) and response.get("subtype") == "error":
            log.warning("assistant: initialize was refused: %s", json.dumps(event)[:300])
        if self._first_prompt is not None:
            return  # Remote Control waits for the first turn, as before
        with self._lock:
            request_remote = not self._remote_requested
            self._remote_requested = True
            if self._pending_turns == 0 and self._status.state == "starting":
                self._status = replace(self._status, state="ready")
        if request_remote:
            self._request_remote_control()
        self._notify()

    def _request_remote_control(self) -> None:
        self._write(
            {
                "type": "control_request",
                "request_id": REMOTE_CONTROL_REQUEST_ID,
                "request": {"subtype": "remote_control", "enabled": True},
            }
        )

    def _on_control_response(self, event: dict[str, object]) -> None:
        request_id = _find_value(event, "request_id")
        if request_id == INITIALIZE_REQUEST_ID:
            self._on_initialized(event)
            return
        if request_id.startswith(SET_MODEL_REQUEST_PREFIX):
            response = event.get("response")
            if isinstance(response, dict) and response.get("subtype") == "error":
                log.warning("assistant: set_model was refused: %s", json.dumps(event)[:300])
            return
        if request_id != REMOTE_CONTROL_REQUEST_ID:
            return
        response = event.get("response")
        subtype = response.get("subtype") if isinstance(response, dict) else None
        url = _find_value(event, "session_url")
        bridge = _find_value(event, "bridge_session_id")
        if subtype == "error" or not url:
            log.warning("assistant: Remote Control was not turned on: %s", json.dumps(event)[:300])
            with self._lock:
                self._status = replace(self._status, remote="failed")
            self._notify()
            return
        with self._lock:
            if self._record is not None:
                self._record = replace(self._record, bridge_session_id=bridge)
                self._store.save(self._record)
            self._status = replace(self._status, session_url=url, remote="on")
        log.info("assistant: Remote Control is on")
        self._notify()

    def _on_result(self, event: dict[str, object]) -> None:
        text = str(event.get("result") or "")
        failed = bool(event.get("is_error"))
        if failed:
            log.warning("assistant: a turn failed: %s", text[:300])
        with self._lock:
            # Decide and store the state under one lock: send() may start a new turn meanwhile.
            self._pending_turns = max(0, self._pending_turns - 1)
            if failed and any(hint in text.lower() for hint in _LOGIN_HINTS):
                self._status = replace(self._status, state="error", error="not_logged_in", detail=text[:300])
                request_remote = False
            else:
                if failed:
                    self._status = replace(self._status, detail=text[:300])
                request_remote = not self._remote_requested
                # After the first turn, like the lab: the session exists on disk by then.
                self._remote_requested = True
                if self._pending_turns == 0 and self._status.state in ("starting", "busy"):
                    self._status = replace(self._status, state="ready")
        if request_remote:
            self._request_remote_control()
        self._notify()
