"""The voicemate tools with fake conversations, a fake Desktop sender and a fake watcher."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.companion.assistant.conversations import ConversationEntry, OwnedConversations, OwnedError, OwnedRecord
from app.companion.assistant.mcp_tools import SERVER_NAME, VoiceMateTools
from app.companion.assistant.session_reader import Message
from app.companion.win.claude_desktop import (
    AmbiguousConversation,
    ConversationMissing,
    DesktopBusy,
    DesktopError,
    DesktopUnavailable,
    FocusLost,
    ScreenLocked,
    WindowMissing,
)


class FakeOwned:
    def __init__(self) -> None:
        self.records = {"own-1": OwnedRecord("own-1", "Mine", "C:\\m", "sonnet", "medium")}
        self.calls: list[tuple[Any, ...]] = []
        self.fail: OwnedError | None = None

    def record(self, conversation_id: str) -> OwnedRecord | None:
        return self.records.get(conversation_id)

    def is_owned(self, conversation_id: str) -> bool:
        return conversation_id in self.records

    def _maybe_fail(self) -> None:
        if self.fail is not None:
            raise self.fail

    def send(self, conversation_id: str, text: str) -> None:
        self._maybe_fail()
        self.calls.append(("send", conversation_id, text))

    def start(self, title: str, prompt: str, model: str, effort: str, cwd: str) -> str:
        self._maybe_fail()
        self.calls.append(("start", title, prompt, model, effort, cwd))
        return "new-1"

    def set_model(self, conversation_id: str, model: str) -> None:
        self.calls.append(("set_model", conversation_id, model))

    def stop(self, conversation_id: str) -> None:
        self.calls.append(("stop", conversation_id))


class FakeWatcher:
    def __init__(self) -> None:
        self.watched: list[tuple[str, str, bool]] = []
        self.unwatched: list[str] = []

    def watch(self, conversation_id: str, title: str, *, from_start: bool = False) -> None:
        self.watched.append((conversation_id, title, from_start))

    def unwatch(self, conversation_id: str) -> None:
        self.unwatched.append(conversation_id)


class FakeDesktop:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def send(self, title: str, text: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((title, text))


ENTRIES = [
    ConversationEntry("desk-1", "Desk", "desktop", "idle", "2026-10-28T10:00:00+00:00", "C:\\d"),
    ConversationEntry("own-1", "Mine", "voicemate", "busy", "2026-10-28T09:00:00+00:00", "C:\\m"),
    ConversationEntry("desk-2", "Twin", "desktop", "idle", "2026-10-28T08:00:00+00:00", "C:\\d"),
    ConversationEntry("desk-3", "Twin", "desktop", "idle", "2026-10-28T07:00:00+00:00", "C:\\d"),
    ConversationEntry("desk-4", "", "desktop", "idle", "2026-10-28T06:00:00+00:00", "C:\\d"),
]


class Rig:
    def __init__(self, tmp_path: Path, desktop: bool = True, defaults: tuple[str, str] = ("sonnet", "medium")) -> None:
        self.owned = FakeOwned()
        self.watcher = FakeWatcher()
        self.desktop = FakeDesktop() if desktop else None
        self.excluded: list[list[str]] = []
        self.transcript = tmp_path / "desk-1.jsonl"
        self.transcript.touch()

        def entries(limit: int, exclude: list[str]) -> list[ConversationEntry]:
            self.excluded.append(exclude)
            return [e for e in ENTRIES if e.id not in exclude][:limit]

        self.server = VoiceMateTools(
            self.owned,  # type: ignore[arg-type]
            self.watcher,  # type: ignore[arg-type]
            desktop=self.desktop,
            central_id=lambda: "central",
            new_defaults=lambda: defaults,
            entries=entries,
            find_file=lambda cid: self.transcript if cid == "desk-1" else None,
            read=lambda _path, n: [Message("user", "q"), Message("assistant", "a" * 5000)][-n:],
        ).server()

    def call(self, name: str, **arguments: object) -> tuple[str, bool]:
        reply = self.server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        )
        assert reply is not None and "result" in reply, reply
        result = reply["result"]
        return result["content"][0]["text"], bool(result.get("isError"))


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_the_server_lists_the_six_tools(rig: Rig) -> None:
    assert rig.server.name == SERVER_NAME == "voicemate"
    assert rig.server.tool_names == [
        "list_conversations",
        "read_conversation",
        "send_to_conversation",
        "start_conversation",
        "set_conversation_model",
        "stop_conversation",
    ]


def test_list_conversations_leaves_out_the_central(rig: Rig) -> None:
    text, failed = rig.call("list_conversations", limit=2)
    assert not failed
    assert [e["id"] for e in json.loads(text)] == ["desk-1", "own-1"]
    assert set(json.loads(text)[0]) == {"id", "title", "owner", "state", "last_activity", "cwd"}
    assert rig.excluded[-1] == ["central"]


def test_read_conversation(rig: Rig) -> None:
    text, failed = rig.call("read_conversation", id="desk-1", last=2)
    assert not failed
    messages = json.loads(text)
    assert messages[0] == {"role": "user", "text": "q"}
    assert len(messages[1]["text"]) == 4000
    assert rig.call("read_conversation", id="nope") == ("no conversation has the id nope", True)


def test_send_to_an_owned_conversation_watches_first(rig: Rig) -> None:
    assert rig.call("send_to_conversation", id="own-1", text="go") == ("sent", False)
    assert rig.owned.calls == [("send", "own-1", "go")]
    assert rig.watcher.watched == [("own-1", "Mine", False)]
    rig.owned.fail = OwnedError("limit", "4 conversations are already running")
    text, failed = rig.call("send_to_conversation", id="own-1", text="go")
    assert failed and "already running" in text
    assert rig.watcher.unwatched == ["own-1"]


def test_send_to_a_desktop_conversation_uses_the_sender(rig: Rig) -> None:
    assert rig.call("send_to_conversation", id="desk-1", text="hello") == ("sent", False)
    assert rig.desktop is not None and rig.desktop.sent == [("Desk", "hello")]
    assert rig.watcher.watched == [("desk-1", "Desk", False)]


def test_desktop_failures_are_clear_errors(rig: Rig) -> None:
    assert rig.desktop is not None
    rig.desktop.error = ScreenLocked("LockApp is in front")
    text, failed = rig.call("send_to_conversation", id="desk-1", text="hello")
    assert failed and text == "not sent (locked): the screen is locked, nothing was typed"
    assert rig.watcher.unwatched == ["desk-1"]
    text, failed = rig.call("send_to_conversation", id="desk-2", text="x")
    assert failed and "ambiguous" in text
    assert rig.call("send_to_conversation", id="desk-4", text="x")[1]
    assert rig.call("send_to_conversation", id="ghost", text="x")[1]
    assert rig.call("send_to_conversation", id="central", text="x") == ("that is this conversation", True)
    assert rig.call("send_to_conversation", id="desk-1", text="  ") == ("the text is empty", True)


def test_without_a_desktop_sender_desktop_sends_are_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path, desktop=False)
    text, failed = rig.call("send_to_conversation", id="desk-1", text="x")
    assert failed and "not available" in text
    assert rig.watcher.watched == []


def test_start_conversation_returns_the_id_and_watches_from_the_start(rig: Rig) -> None:
    text, failed = rig.call("start_conversation", title=" Research ", prompt="look")
    assert not failed
    assert json.loads(text) == {"id": "new-1", "title": "Research"}
    assert rig.owned.calls == [("start", " Research ", "look", "sonnet", "medium", "")]
    assert rig.watcher.watched == [("new-1", "Research", True)]
    rig.owned.fail = OwnedError("limit", "too many")
    assert rig.call("start_conversation", title="x", prompt="y") == ("too many", True)


def test_model_and_stop_only_for_owned(rig: Rig) -> None:
    assert rig.call("set_conversation_model", id="own-1", model="opus") == ("done", False)
    text, failed = rig.call("set_conversation_model", id="desk-1", model="opus")
    assert failed and text.startswith("not possible")
    assert rig.call("stop_conversation", id="own-1") == ("stopped", False)
    assert rig.call("stop_conversation", id="desk-1")[1]
    assert ("stop", "own-1") in rig.owned.calls


def test_real_owned_conversations_satisfy_the_tools(tmp_path: Path) -> None:
    # The real manager plugs in without adapters (types checked by mypy, smoke-checked here).
    tools = VoiceMateTools(OwnedConversations(tmp_path), FakeWatcher(), entries=lambda _n, _e: [])  # type: ignore[arg-type]
    assert tools.server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) is not None


@pytest.mark.parametrize(
    ("error", "code", "words"),
    [
        (WindowMissing(), "window_missing", "window was not found"),
        (ConversationMissing(), "conversation_missing", "sidebar"),
        (AmbiguousConversation(), "ambiguous", "more than one"),
        (FocusLost(), "focus_lost", "nothing was typed"),
        (ScreenLocked(), "locked", "screen is locked"),
        (DesktopBusy(), "busy", "busy"),
        (DesktopUnavailable(), "unavailable", "not available"),
    ],
)
def test_every_desktop_error_code_has_a_clear_reason(rig: Rig, error: DesktopError, code: str, words: str) -> None:
    assert rig.desktop is not None
    rig.desktop.error = error
    text, failed = rig.call("send_to_conversation", id="desk-1", text="hello")
    assert failed and text.startswith(f"not sent ({code}): ") and words in text
    assert rig.watcher.unwatched == ["desk-1"]


def test_an_empty_title_refused_by_the_sender_is_an_error(rig: Rig) -> None:
    assert rig.desktop is not None
    rig.desktop.error = ValueError("title and text must not be empty")
    text, failed = rig.call("send_to_conversation", id="desk-1", text="hello")
    assert failed and text.startswith("not sent: ")


def test_start_conversation_falls_back_to_the_new_conversation_defaults(tmp_path: Path) -> None:
    rig = Rig(tmp_path, defaults=("opus", "high"))
    rig.call("start_conversation", title="A", prompt="p")
    rig.call("start_conversation", title="B", prompt="p", model="haiku")
    rig.call("start_conversation", title="C", prompt="p", effort="low")
    assert [call[3:5] for call in rig.owned.calls] == [("opus", "high"), ("haiku", "high"), ("opus", "low")]
