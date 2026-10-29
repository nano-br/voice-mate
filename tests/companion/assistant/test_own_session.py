"""The own session port with a fake child process (no real CLI)."""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.companion.assistant.contract import PortStatus
from app.companion.assistant.mcp_server import McpServer, Tool
from app.companion.assistant.own_session import (
    FIRST_PROMPT,
    OwnSessionPort,
    SessionRecord,
    SessionStateStore,
    build_command,
    find_cli,
    scrubbed_env,
)
from app.companion.assistant.session_reader import LiveSession
from app.companion.contract import AssistantSettings

SETTINGS = AssistantSettings(mode="own_session", model="sonnet", effort="medium")


class FakeChild:
    def __init__(self, command: list[str], cwd: Path, env: dict[str, str]) -> None:
        self.command, self.cwd, self.env = command, cwd, env
        self.written: list[dict[str, Any]] = []
        self.out: queue.Queue[str | None] = queue.Queue()
        self.stdin_closed = threading.Event()
        self.killed = False
        self.exits_on_close = True
        self.broken = False

    def write_line(self, line: str) -> None:
        if self.broken:
            raise OSError("broken pipe")
        self.written.append(json.loads(line))

    def read_line(self) -> str | None:
        return self.out.get()

    def close_stdin(self) -> None:
        self.stdin_closed.set()
        if self.exits_on_close:
            self.out.put(None)

    def wait(self, timeout: float) -> int | None:
        return 0 if self.exits_on_close or self.killed else None

    def kill(self) -> None:
        self.killed = True
        self.out.put(None)

    def emit(self, **event: object) -> None:
        self.out.put(json.dumps(event) + "\n")


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        *,
        find_cli_fn: Callable[[], str | None] = lambda: "claude-cli",
        sessions: Callable[[], dict[str, LiveSession]] = dict,
        transcript_exists: Callable[[str], bool] = lambda _sid: True,
        **port_kwargs: Any,  # noqa: ANN401 - forwarded to OwnSessionPort
    ) -> None:
        self.children: list[FakeChild] = []
        self.changed = threading.Condition()
        self.state_path = tmp_path / "state" / "assistant-session.json"
        self.cwd = tmp_path / "cwd"

        def spawn(command: list[str], cwd: Path, env: dict[str, str]) -> FakeChild:
            child = FakeChild(command, cwd, env)
            self.children.append(child)
            return child

        self.port = OwnSessionPort(
            SETTINGS,
            cwd=self.cwd,
            state_path=self.state_path,
            spawn=spawn,
            find_cli_fn=find_cli_fn,
            environ={"PATH": "x", "CLAUDE_CODE_ENTRYPOINT": "desktop", "CLAUDE_CODE_OAUTH_TOKEN": "t"},
            sessions=sessions,
            transcript_exists=transcript_exists,
            **port_kwargs,
        )

    def on_change(self, status: PortStatus) -> None:
        with self.changed:
            self.changed.notify_all()

    def start(self) -> FakeChild:
        self.port.start(self.on_change)
        return self.children[-1]

    def wait_for(self, predicate: Callable[[PortStatus], bool], timeout: float = 3.0) -> None:
        with self.changed:
            assert self.changed.wait_for(lambda: predicate(self.port.status()), timeout), self.port.status()


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_command_has_the_configured_model_effort_and_name() -> None:
    command = build_command("claude", SETTINGS)
    assert command[0] == "claude"
    assert command[1:4] == ["-p", "--input-format", "stream-json"]
    assert command[command.index("--model") + 1] == "sonnet"
    assert command[command.index("--effort") + 1] == "medium"
    assert command[command.index("--name") + 1] == "VoiceMate"
    assert "--resume" not in command
    assert build_command("claude", SETTINGS, "abc")[-2:] == ["--resume", "abc"]


def test_scrubbed_env_drops_host_variables_and_keeps_the_chosen_ones() -> None:
    env = {
        "PATH": "p",
        "CLAUDECODE": "1",
        "CLAUDE_PID": "7",
        "CLAUDE_CODE_ENTRYPOINT": "x",
        "CLAUDE_CODE_NEW_THING": "y",
        "CLAUDE_CODE_OAUTH_TOKEN": "tok",
        "CLAUDE_CONFIG_DIR": "c",
        "CLAUDE_CODE_GIT_BASH_PATH": "b",
    }
    assert scrubbed_env(env) == {
        "PATH": "p",
        "CLAUDE_CODE_OAUTH_TOKEN": "tok",
        "CLAUDE_CONFIG_DIR": "c",
        "CLAUDE_CODE_GIT_BASH_PATH": "b",
    }


def test_find_cli_prefers_the_override_then_path(tmp_path: Path) -> None:
    exe = tmp_path / "my-claude"
    exe.write_text("", encoding="utf-8")
    assert find_cli({}, lambda name: f"/bin/{name}" if name == "claude" else None) == "/bin/claude"
    assert find_cli({"VOICEMATE_CLAUDE_CLI": str(exe)}, lambda _n: None) == str(exe)
    assert find_cli({"VOICEMATE_CLAUDE_CLI": "/nope"}, lambda _n: "/bin/claude") == "/bin/claude"
    assert find_cli({"VOICEMATE_CLAUDE_CLI": "/nope"}, lambda _n: None) is None
    assert find_cli({}, lambda _n: None) is None


def test_missing_cli_is_an_error_state(tmp_path: Path) -> None:
    rig = Rig(tmp_path, find_cli_fn=lambda: None)
    rig.port.start(rig.on_change)
    status = rig.port.status()
    assert (status.state, status.error) == ("error", "cli_missing")
    assert rig.children == []


def test_start_sends_the_skill_prompt_then_remote_control_and_becomes_ready(rig: Rig) -> None:
    child = rig.start()
    assert rig.port.status().state == "starting"
    assert "CLAUDE_CODE_ENTRYPOINT" not in child.env
    assert child.env["CLAUDE_CODE_OAUTH_TOKEN"] == "t"
    assert child.cwd == rig.cwd
    init = child.written[0]
    assert init["type"] == "control_request"
    assert init["request"] == {"subtype": "initialize", "hooks": None}
    assert child.written[1]["message"] == {"role": "user", "content": FIRST_PROMPT}

    child.emit(type="system", subtype="init", session_id="sess-1")
    rig.wait_for(lambda s: s.session_id == "sess-1")
    child.emit(type="result", subtype="success", is_error=False, result="hi", session_id="sess-1")
    rig.wait_for(lambda s: s.state == "ready")
    rc = child.written[2]
    assert rc["type"] == "control_request"
    assert rc["request"] == {"subtype": "remote_control", "enabled": True}

    child.emit(
        type="control_response",
        response={
            "subtype": "success",
            "request_id": rc["request_id"],
            "response": {"session_url": "https://claude.ai/code/session_x", "bridge_session_id": "br-1"},
        },
    )
    rig.wait_for(lambda s: s.remote == "on")
    assert rig.port.status().session_url == "https://claude.ai/code/session_x"
    assert SessionStateStore(rig.state_path).load() == SessionRecord("sess-1", "br-1", str(rig.cwd))


def test_remote_control_failure_keeps_the_local_session(rig: Rig) -> None:
    child = rig.start()
    child.emit(type="result", is_error=False, result="hi")
    rig.wait_for(lambda s: s.state == "ready")
    child.emit(type="control_response", response={"subtype": "error", "request_id": "voicemate-remote-control"})
    rig.wait_for(lambda s: s.remote == "failed")
    assert rig.port.status().state == "ready"


def test_send_marks_busy_until_the_result(rig: Rig) -> None:
    child = rig.start()
    child.emit(type="result", is_error=False, result="hi")
    rig.wait_for(lambda s: s.state == "ready")
    assert rig.port.send("hello there")
    assert child.written[-1] == {"type": "user", "message": {"role": "user", "content": "hello there"}}
    assert rig.port.status().state == "busy"
    child.emit(type="result", is_error=False, result="ok")
    rig.wait_for(lambda s: s.state == "ready")


def test_send_without_a_child_is_refused(rig: Rig) -> None:
    assert rig.port.send("hello") is False


def test_login_failure_is_reported(rig: Rig) -> None:
    child = rig.start()
    child.emit(type="result", is_error=True, result="Not logged in. Please run /login")
    rig.wait_for(lambda s: s.state == "error")
    assert rig.port.status().error == "not_logged_in"


def test_process_ending_by_itself_is_an_error_with_its_output(rig: Rig) -> None:
    child = rig.start()
    child.out.put("boom: something broke\n")
    child.out.put(None)
    rig.wait_for(lambda s: s.state == "error")
    status = rig.port.status()
    assert status.error == "exited"
    assert "something broke" in status.detail
    assert rig.port.send("again") is False


def test_stop_closes_stdin_and_reports_stopped(rig: Rig) -> None:
    child = rig.start()
    rig.port.stop()
    assert child.stdin_closed.is_set()
    assert not child.killed
    assert rig.port.status().state == "stopped"


def test_stop_kills_a_process_that_does_not_end(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.companion.assistant.own_session.STOP_WAIT_S", 0.01)
    monkeypatch.setattr("app.companion.assistant.own_session.KILL_WAIT_S", 0.5)
    child = rig.start()
    child.exits_on_close = False
    rig.port.stop()
    assert child.killed
    assert rig.port.status().state == "stopped"


def test_a_broken_pipe_refuses_the_prompt(rig: Rig) -> None:
    child = rig.start()
    child.broken = True
    assert rig.port.send("hello") is False


def test_a_failed_write_undoes_the_busy_state(rig: Rig) -> None:
    child = rig.start()
    child.emit(type="result", is_error=False, result="hi")
    rig.wait_for(lambda s: s.state == "ready")
    child.broken = True
    assert rig.port.send("lost") is False
    assert rig.port.status().state == "ready"
    child.broken = False
    assert rig.port.send("again")
    child.emit(type="result", is_error=False, result="ok")
    rig.wait_for(lambda s: s.state == "ready")


def test_a_result_does_not_free_the_port_while_another_turn_is_pending(rig: Rig) -> None:
    child = rig.start()
    child.emit(type="result", is_error=False, result="hi")
    rig.wait_for(lambda s: s.state == "ready")
    assert rig.port.send("one")
    assert rig.port.send("two")
    child.emit(type="result", is_error=False, result="first")
    with rig.changed:
        rig.changed.wait(0.3)
    assert rig.port.status().state == "busy"
    child.emit(type="result", is_error=False, result="second")
    rig.wait_for(lambda s: s.state == "ready")


def test_resume_uses_the_stored_id_when_no_live_process_owns_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    SessionStateStore(rig.state_path).save(SessionRecord("old-id", "br", str(rig.cwd)))
    child = rig.start()
    assert child.command[-2:] == ["--resume", "old-id"]
    assert rig.port.status().session_id == "old-id"


def test_a_live_owner_means_a_new_session(tmp_path: Path) -> None:
    live = {"old-id": LiveSession("old-id", 123, "idle")}
    rig = Rig(tmp_path, sessions=lambda: live)
    SessionStateStore(rig.state_path).save(SessionRecord("old-id", "", str(rig.cwd)))
    assert "--resume" not in rig.start().command


def test_no_transcript_or_another_cwd_means_a_new_session(tmp_path: Path) -> None:
    rig = Rig(tmp_path, transcript_exists=lambda _sid: False)
    SessionStateStore(rig.state_path).save(SessionRecord("old-id", "", str(rig.cwd)))
    assert "--resume" not in rig.start().command

    other = Rig(tmp_path / "other")
    SessionStateStore(other.state_path).save(SessionRecord("old-id", "", "somewhere else"))
    assert "--resume" not in other.start().command


def test_state_store_survives_garbage(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    store = SessionStateStore(path)
    assert store.load() is None
    path.write_text("not json", encoding="utf-8")
    assert store.load() is None
    path.write_text(json.dumps({"session_id": 3}), encoding="utf-8")
    assert store.load() is None
    store.save(SessionRecord("a", "b", "c"))
    assert store.load() == SessionRecord("a", "b", "c")
    store.clear()
    assert store.load() is None


# --- sdk MCP servers, set_model, permission mode, conversations without a first prompt -------


def _echo_server() -> McpServer:
    tool = Tool(
        "echo",
        "Echo a word.",
        {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]},
        lambda args: f"echo {args['word']}",
    )
    return McpServer("voicemate", [tool])


def _wait_written(
    child: FakeChild, predicate: Callable[[dict[str, Any]], bool], timeout: float = 3.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for message in list(child.written):
            if predicate(message):
                return message
        time.sleep(0.01)
    raise AssertionError(f"not written: {child.written}")


def test_command_carries_permission_mode_session_id_and_allowed_mcp_tools() -> None:
    command = build_command(
        "claude",
        SETTINGS,
        model="claude-haiku-5-5",
        effort="low",
        name="Research",
        permission_mode="acceptEdits",
        session_id="11111111-2222-3333-4444-555555555555",
        mcp_servers=["voicemate"],
    )
    assert command[command.index("--model") + 1] == "claude-haiku-5-5"
    assert command[command.index("--effort") + 1] == "low"
    assert command[command.index("--name") + 1] == "Research"
    assert command[command.index("--permission-mode") + 1] == "acceptEdits"
    assert command[command.index("--allowedTools") + 1] == "mcp__voicemate"
    assert command[command.index("--session-id") + 1] == "11111111-2222-3333-4444-555555555555"
    resumed = build_command("claude", SETTINGS, "old", session_id="new")
    assert "--session-id" not in resumed
    assert "--permission-mode" not in build_command("claude", SETTINGS)


def test_initialize_declares_the_sdk_servers_and_mcp_messages_are_answered(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mcp_servers=[_echo_server()])
    child = rig.start()
    assert child.written[0]["request"] == {"subtype": "initialize", "hooks": None, "sdkMcpServers": ["voicemate"]}
    assert "mcp__voicemate" in child.command

    call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "echo", "arguments": {"word": "hi"}}}
    child.emit(
        type="control_request",
        request_id="req-1",
        request={"subtype": "mcp_message", "server_name": "voicemate", "message": call},
    )
    reply = _wait_written(child, lambda m: m.get("type") == "control_response")
    assert reply["response"]["subtype"] == "success"
    assert reply["response"]["request_id"] == "req-1"
    mcp = reply["response"]["response"]["mcp_response"]
    assert mcp == {"jsonrpc": "2.0", "id": 7, "result": {"content": [{"type": "text", "text": "echo hi"}]}}


def test_a_notification_and_an_unknown_server_still_get_an_answer(tmp_path: Path) -> None:
    rig = Rig(tmp_path, mcp_servers=[_echo_server()])
    child = rig.start()
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    child.emit(
        type="control_request",
        request_id="n1",
        request={"subtype": "mcp_message", "server_name": "voicemate", "message": note},
    )
    ack = _wait_written(child, lambda m: m.get("response", {}).get("request_id") == "n1")
    assert ack["response"]["response"] == {"mcp_response": {"jsonrpc": "2.0", "result": {}}}

    ping = {"jsonrpc": "2.0", "id": 3, "method": "ping"}
    child.emit(
        type="control_request",
        request_id="u1",
        request={"subtype": "mcp_message", "server_name": "other", "message": ping},
    )
    unknown = _wait_written(child, lambda m: m.get("response", {}).get("request_id") == "u1")
    assert unknown["response"]["response"]["mcp_response"]["error"]["code"] == -32601


def test_other_control_requests_are_refused(rig: Rig) -> None:
    child = rig.start()
    child.emit(type="control_request", request_id="p1", request={"subtype": "can_use_tool", "tool_name": "Bash"})
    refused = _wait_written(child, lambda m: m.get("response", {}).get("request_id") == "p1")
    assert refused["response"]["subtype"] == "error"


def test_set_model_sends_the_control_request_and_is_kept_for_a_restart(rig: Rig) -> None:
    assert rig.port.set_model("opus") is False  # nothing running
    child = rig.start()
    assert rig.port.set_model("opus")
    request = child.written[-1]
    assert request["type"] == "control_request"
    assert request["request"] == {"subtype": "set_model", "model": "opus"}
    rig.port.stop()
    again = rig.start()
    assert again.command[again.command.index("--model") + 1] == "opus"


def test_without_a_first_prompt_the_port_is_ready_after_initialize_and_asks_remote_control(tmp_path: Path) -> None:
    rig = Rig(tmp_path, first_prompt=None)
    child = rig.start()
    assert [m["type"] for m in child.written] == ["control_request"]
    child.emit(
        type="control_response", response={"subtype": "success", "request_id": "voicemate-initialize", "response": {}}
    )
    rig.wait_for(lambda s: s.state == "ready")
    rc = _wait_written(child, lambda m: m.get("request", {}).get("subtype") == "remote_control")
    assert rc["request"]["enabled"] is True


def test_a_new_session_id_is_known_and_saved_at_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path, new_session_id=lambda: "fixed-id")
    child = rig.start()
    assert child.command[child.command.index("--session-id") + 1] == "fixed-id"
    assert rig.port.status().session_id == "fixed-id"
    assert SessionStateStore(rig.state_path).load() == SessionRecord("fixed-id", "", str(rig.cwd))


def test_a_store_can_replace_the_state_file(tmp_path: Path) -> None:
    saved: list[SessionRecord] = []

    class Store:
        def load(self) -> SessionRecord | None:
            return None

        def save(self, record: SessionRecord) -> None:
            saved.append(record)

        def clear(self) -> None:
            pass

    port = OwnSessionPort(
        SETTINGS, cwd=tmp_path, store=Store(), spawn=FakeChild, find_cli_fn=lambda: "c", new_session_id=lambda: "x"
    )
    port.start(lambda _s: None)
    assert saved == [SessionRecord("x", "", str(tmp_path))]
    port.stop()
    with pytest.raises(ValueError):
        OwnSessionPort(SETTINGS, cwd=tmp_path)
