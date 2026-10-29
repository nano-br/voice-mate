"""The Desktop conversation port with a fake sender and real transcript files (no window is touched)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from app.companion.assistant.contract import ConversationPort, PortStatus
from app.companion.assistant.desktop_port import DesktopConversationPort
from app.companion.assistant.own_session import FIRST_PROMPT
from app.companion.assistant.session_reader import ConversationInfo, LiveSession
from app.companion.win.claude_desktop import ConversationMissing, FocusLost

TITLE = "VoiceMate central"
SID = "00000000-0000-0000-0000-00000000c0de"


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.error: Exception | None = None
        self.block = threading.Event()
        self.block.set()

    def send(self, title: str, text: str) -> None:
        self.block.wait(5)
        if self.error is not None:
            raise self.error
        self.calls.append((title, text))


def _append(path: Path, *events: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        for event in events:
            file.write(json.dumps(event) + "\n")


def user(text: str) -> dict[str, object]:
    return {"type": "user", "uuid": "u" + text, "message": {"role": "user", "content": text}}


def reply(text: str, stop: str | None = "end_turn") -> dict[str, object]:
    return {
        "type": "assistant",
        "uuid": "a" + text,
        "message": {
            "id": "m" + text,
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop,
        },
    }


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / f"{SID}.jsonl"
        self.path.write_text(json.dumps({"type": "custom-title", "customTitle": TITLE}) + "\n", encoding="utf-8")
        self.sender = FakeSender()
        self.live: dict[str, LiveSession] = {}
        self.found = [ConversationInfo(SID, self.path, TITLE, 1.0, "p")]
        self.statuses: list[PortStatus] = []
        self.lookups: list[str] = []

    def port(self, title: str = TITLE, **kwargs: object) -> DesktopConversationPort:
        def find(wanted: str) -> list[ConversationInfo]:
            self.lookups.append(wanted)
            return self.found

        return DesktopConversationPort(
            title,
            self.sender,
            find_sessions=find,
            sessions=lambda: self.live,
            poll_s=0.01,
            **kwargs,  # type: ignore[arg-type]
        )

    def wait_for(
        self, port: ConversationPort, predicate: Callable[[PortStatus], bool], timeout: float = 5.0
    ) -> PortStatus:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = port.status()
            if predicate(status):
                return status
            time.sleep(0.01)
        raise AssertionError(f"timeout, last status {port.status()}")


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


def test_start_sends_the_slash_command_once_then_goes_ready_after_the_turn(h: Harness) -> None:
    port = h.port()
    port.start(h.statuses.append)
    try:
        h.wait_for(port, lambda s: bool(h.sender.calls))
        assert h.sender.calls == [(TITLE, FIRST_PROMPT)]
        status = h.wait_for(port, lambda s: s.state == "busy")  # the prompt has not shown in the file yet
        assert status.session_id == SID
        _append(h.path, user(FIRST_PROMPT), reply("hello"))
        status = h.wait_for(port, lambda s: s.state == "ready")
        assert status.session_id == SID
        assert h.lookups == [TITLE]
        assert [s.state for s in h.statuses][0] == "starting"
    finally:
        port.stop()
    assert port.status().state == "stopped"


def test_a_send_goes_through_the_flash_in_order_and_makes_the_port_busy(h: Harness) -> None:
    port = h.port()
    port.start(h.statuses.append)
    try:
        _append(h.path, user(FIRST_PROMPT), reply("hello"))
        h.wait_for(port, lambda s: s.state == "ready")
        assert port.send("what is next?") is True
        h.wait_for(port, lambda s: len(h.sender.calls) == 2)
        assert h.sender.calls == [(TITLE, FIRST_PROMPT), (TITLE, "what is next?")]
        h.wait_for(port, lambda s: s.state == "busy")
        _append(h.path, user("what is next?"), reply("tool work", stop="tool_use"))
        time.sleep(0.1)
        assert port.status().state == "busy"  # the turn is still open
        _append(h.path, reply("done"))
        h.wait_for(port, lambda s: s.state == "ready")
    finally:
        port.stop()


def test_a_live_busy_session_keeps_the_port_busy(h: Harness) -> None:
    port = h.port(send_first_prompt=False)
    port.start(h.statuses.append)
    try:
        h.wait_for(port, lambda s: s.state == "ready")
        h.live[SID] = LiveSession(SID, 1, "busy", TITLE)
        h.wait_for(port, lambda s: s.state == "busy")
        h.live[SID] = LiveSession(SID, 1, "idle", TITLE)
        h.wait_for(port, lambda s: s.state == "ready")
        assert h.sender.calls == []  # no first prompt when asked not to
    finally:
        port.stop()


def test_no_conversation_configured(h: Harness) -> None:
    port = h.port(title="  ")
    port.start(h.statuses.append)
    status = h.wait_for(port, lambda s: s.state == "error")
    assert status.error == "setup_failed"
    assert port.send("hi") is False
    assert h.sender.calls == []
    assert h.lookups == []
    port.stop()
    assert port.status().state == "stopped"


def test_no_transcript_with_that_title(h: Harness) -> None:
    h.found = []
    port = h.port()
    port.start(h.statuses.append)
    status = h.wait_for(port, lambda s: s.state == "error")
    assert status.error == "setup_failed"
    assert TITLE in status.detail
    assert port.send("hi") is False
    assert h.sender.calls == []
    port.stop()


def test_the_newest_of_several_transcripts_is_followed(h: Harness, tmp_path: Path) -> None:
    other = ConversationInfo("older-id", tmp_path / "older.jsonl", TITLE, 0.5, "p")
    h.found = [ConversationInfo(SID, h.path, TITLE, 2.0, "p"), other]
    port = h.port(send_first_prompt=False)
    port.start(h.statuses.append)
    try:
        assert h.wait_for(port, lambda s: s.state == "ready").session_id == SID
    finally:
        port.stop()


def test_a_failed_flash_on_the_first_prompt_is_a_start_failure(h: Harness) -> None:
    h.sender.error = ConversationMissing("no conversation titled 'VoiceMate central' in the sidebar")
    port = h.port()
    port.start(h.statuses.append)
    status = h.wait_for(port, lambda s: s.state == "error")
    assert status.error == "start_failed"
    assert "conversation_missing" in status.detail
    assert status.session_id == SID
    assert port.send("hi") is False
    port.stop()


def test_a_failed_flash_on_a_later_send_reports_the_reason_and_the_text(h: Harness) -> None:
    failed: list[str] = []
    port = h.port(send_first_prompt=False, on_failed=failed.append)
    port.start(h.statuses.append)
    try:
        h.wait_for(port, lambda s: s.state == "ready")
        h.sender.error = FocusLost("the Claude window is not in the foreground")
        assert port.send("hi") is True  # queued: the result comes later
        status = h.wait_for(port, lambda s: s.state == "error")
        assert failed == ["hi"]
        assert status.error == "start_failed"
        assert "focus_lost" in status.detail
        assert port.send("again") is False  # the service restarts a port in error
    finally:
        port.stop()


def test_an_unexpected_exception_from_the_sender_is_an_error_not_a_dead_thread(h: Harness) -> None:
    port = h.port(send_first_prompt=False)
    port.start(h.statuses.append)
    try:
        h.wait_for(port, lambda s: s.state == "ready")
        h.sender.error = RuntimeError("COMError")
        assert port.send("hi") is True
        assert "COMError" in h.wait_for(port, lambda s: s.state == "error").detail
    finally:
        port.stop()


def test_send_before_start_or_after_stop_is_false(h: Harness) -> None:
    port = h.port()
    assert port.send("hi") is False
    port.start(h.statuses.append)
    port.stop()
    assert port.send("hi") is False
    assert port.status().state == "stopped"


def test_send_never_waits_for_a_flash_in_progress(h: Harness) -> None:
    h.sender.block.clear()  # the first flash hangs
    port = h.port()
    port.start(h.statuses.append)
    try:
        h.wait_for(port, lambda s: s.state == "starting" and bool(s.session_id))
        began = time.monotonic()
        assert port.send("queued") is True
        assert time.monotonic() - began < 0.1
    finally:
        h.sender.block.set()
        port.stop()
    assert port.status().state == "stopped"


def test_a_stop_that_gives_up_on_a_stuck_flash_hands_back_what_was_queued(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.companion.assistant.desktop_port.STOP_WAIT_S", 0.2)
    h.sender.block.clear()  # the first flash hangs
    failed: list[str] = []
    port = h.port(on_failed=failed.append)
    port.start(h.statuses.append)
    h.wait_for(port, lambda s: s.state == "starting" and bool(s.session_id))
    assert port.send("queued") is True
    workers = [t for t in threading.enumerate() if t.name == "desktop-port"]
    port.stop()
    assert failed == ["queued"]  # the caller keeps the text instead of losing it
    h.sender.block.set()
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()  # the end marker is still there for the worker that was stuck
