"""The `voicemate` sdk MCP server: the tools the central conversation (mode own_session) uses to
work with other conversations.

- `list_conversations`, `read_conversation`: from the transcripts on disk (session_reader.py).
- `send_to_conversation`: a VoiceMate-owned conversation gets the prompt on its stdin; a Desktop
  one through a `ConversationSender` (flash typing, app/companion/win/claude_desktop.py), injected;
  each `DesktopError` code becomes a plain reason for the central.
- `start_conversation`, `set_conversation_model`, `stop_conversation`: VoiceMate-owned only
  (conversations.py). A start without a model or effort uses the person's defaults for new
  conversations (`new_defaults`).

Writing tools never wait for the answer: they register the conversation with the
`AnswerWatcher` (notifier.py), which tells the central when it answered. Results are JSON text.
Standard library only.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from app.companion.assistant.conversations import ConversationEntry, OwnedConversations, OwnedError, list_entries
from app.companion.assistant.mcp_server import JsonObject, McpServer, Tool, ToolError
from app.companion.assistant.notifier import AnswerWatcher
from app.companion.assistant.session_reader import Message, find_session_file, last_messages
from app.companion.win.claude_desktop import ConversationSender, DesktopError

log = logging.getLogger(__name__)

SERVER_NAME: Final = "voicemate"
SERVER_VERSION: Final = "1.0.0"
MESSAGE_LIMIT: Final = 4000  # characters of one message read_conversation returns
READ_LIMIT: Final = 50

_DESKTOP_REASONS: Final[dict[str, str]] = {
    "window_missing": "the Claude Desktop window was not found (is the app open?)",
    "conversation_missing": "the conversation was not found in the Claude Desktop sidebar",
    "ambiguous": "more than one Desktop conversation has that title",
    "focus_lost": "the Desktop prompt did not keep the focus, nothing was typed",
    "locked": "the screen is locked, nothing was typed",
    "busy": "Claude Desktop is busy with another message, try again in a moment",
    "unavailable": "writing to Claude Desktop is not available on this computer",
}


def desktop_reason(error: DesktopError) -> str:
    """English, for the central: why a Desktop delivery failed."""
    return _DESKTOP_REASONS.get(error.code, "Claude Desktop did not take the message")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


@dataclass(frozen=True)
class _Deps:
    owned: OwnedConversations
    watcher: AnswerWatcher
    desktop: ConversationSender | None
    central_id: Callable[[], str]
    new_defaults: Callable[[], tuple[str, str]]
    entries: Callable[[int, list[str]], list[ConversationEntry]]
    find_file: Callable[[str], Path | None]
    read: Callable[[Path, int], list[Message]]


class VoiceMateTools:
    """Builds the `voicemate` server. `central_id` returns the central conversation's session id
    (it is left out of the list and cannot be written to through the tools); `new_defaults`
    returns the (model, effort) a new conversation gets when the central passes none."""

    def __init__(
        self,
        owned: OwnedConversations,
        watcher: AnswerWatcher,
        *,
        desktop: ConversationSender | None = None,
        central_id: Callable[[], str] = lambda: "",
        new_defaults: Callable[[], tuple[str, str]] = lambda: ("sonnet", "medium"),
        entries: Callable[[int, list[str]], list[ConversationEntry]] | None = None,
        find_file: Callable[[str], Path | None] = find_session_file,
        read: Callable[[Path, int], list[Message]] = last_messages,
    ) -> None:
        self._d = _Deps(
            owned,
            watcher,
            desktop,
            central_id,
            new_defaults,
            entries or (lambda limit, exclude: list_entries(owned, limit, exclude=exclude)),
            find_file,
            read,
        )

    def server(self) -> McpServer:
        return McpServer(SERVER_NAME, self.tools(), SERVER_VERSION)

    def tools(self) -> list[Tool]:
        string = {"type": "string"}
        conv_id = {"type": "string", "description": "The conversation id (from list_conversations)."}
        return [
            Tool(
                "list_conversations",
                "List the Claude Desktop conversations and the ones VoiceMate started, newest first: "
                "id, title, owner (desktop or voicemate), state (busy, idle or waiting), last activity, cwd.",
                {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 20, "description": "At most this many."}},
                },
                self._list,
            ),
            Tool(
                "read_conversation",
                "Read the last messages (role and text) of a conversation.",
                {
                    "type": "object",
                    "properties": {"id": conv_id, "last": {"type": "integer", "default": 10}},
                    "required": ["id"],
                },
                self._read,
            ),
            Tool(
                "send_to_conversation",
                "Send a prompt to a conversation. Returns at once; VoiceMate tells you when it answers.",
                {"type": "object", "properties": {"id": conv_id, "text": string}, "required": ["id", "text"]},
                self._send,
            ),
            Tool(
                "start_conversation",
                "Start a new conversation owned by VoiceMate (visible in the Desktop and on the phone) "
                "with a first prompt; returns its id. VoiceMate tells you when it answers.",
                {
                    "type": "object",
                    "properties": {
                        "title": string,
                        "prompt": string,
                        "model": {
                            "type": "string",
                            "description": "haiku, sonnet or opus; leave it out for the person's default.",
                        },
                        "effort": {
                            "type": "string",
                            "description": "low, medium or high; leave it out for the person's default.",
                        },
                        "cwd": {"type": "string", "default": "", "description": "Working folder; empty = VoiceMate's."},
                    },
                    "required": ["title", "prompt"],
                },
                self._start,
            ),
            Tool(
                "set_conversation_model",
                "Change the model of a conversation VoiceMate owns (not possible for Desktop ones).",
                {"type": "object", "properties": {"id": conv_id, "model": string}, "required": ["id", "model"]},
                self._set_model,
            ),
            Tool(
                "stop_conversation",
                "Stop a conversation VoiceMate owns (it is not resumed on the next start).",
                {"type": "object", "properties": {"id": conv_id}, "required": ["id"]},
                self._stop,
            ),
        ]

    # --- handlers ------------------------------------------------------------------------------

    def _excluded(self) -> list[str]:
        central = self._d.central_id()
        return [central] if central else []

    def _list(self, args: JsonObject) -> str:
        limit = max(1, min(int(args["limit"]), 100))
        return _json([entry.as_json() for entry in self._d.entries(limit, self._excluded())])

    def _read(self, args: JsonObject) -> str:
        conversation_id = str(args["id"])
        path = self._d.find_file(conversation_id)
        if path is None:
            raise ToolError(f"no conversation has the id {conversation_id}")
        last = max(1, min(int(args["last"]), READ_LIMIT))
        messages = self._d.read(path, last)
        return _json([{"role": m.role, "text": _clip(m.text)} for m in messages])

    def _send(self, args: JsonObject) -> str:
        conversation_id, text = str(args["id"]), str(args["text"])
        if not text.strip():
            raise ToolError("the text is empty")
        if conversation_id in self._excluded():
            raise ToolError("that is this conversation")
        owned = self._d.owned.record(conversation_id)
        if owned is not None:
            self._d.watcher.watch(conversation_id, owned.title)
            try:
                self._d.owned.send(conversation_id, text)
            except OwnedError as exc:
                self._d.watcher.unwatch(conversation_id)
                raise ToolError(str(exc)) from exc
            return "sent"
        entry = self._desktop_entry(conversation_id)
        if self._d.desktop is None:
            raise ToolError("writing to Claude Desktop conversations is not available")
        self._d.watcher.watch(conversation_id, entry.title)
        try:
            self._d.desktop.send(entry.title, text)
        except DesktopError as exc:
            self._d.watcher.unwatch(conversation_id)
            log.info("voicemate tools: Desktop send failed (%s)", exc.code)
            raise ToolError(f"not sent ({exc.code}): {desktop_reason(exc)}") from exc
        except ValueError as exc:  # an empty title or text
            self._d.watcher.unwatch(conversation_id)
            raise ToolError(f"not sent: {exc}") from exc
        return "sent"

    def _desktop_entry(self, conversation_id: str) -> ConversationEntry:
        entries = self._d.entries(100, self._excluded())
        entry = next((e for e in entries if e.id == conversation_id), None)
        if entry is None or entry.owner != "desktop":
            raise ToolError(f"no Desktop or VoiceMate conversation has the id {conversation_id}")
        if not entry.title:
            raise ToolError("that Desktop conversation has no title, it cannot be found in the sidebar")
        if sum(1 for e in entries if e.owner == "desktop" and e.title == entry.title) > 1:
            raise ToolError(f"not sent (ambiguous): more than one Desktop conversation is called {entry.title!r}")
        return entry

    def _start(self, args: JsonObject) -> str:
        title = str(args["title"])
        model, effort = self._d.new_defaults()
        model = str(args.get("model") or "").strip() or model
        effort = str(args.get("effort") or "").strip() or effort
        try:
            conversation_id = self._d.owned.start(title, str(args["prompt"]), model, effort, str(args["cwd"]))
        except OwnedError as exc:
            raise ToolError(str(exc)) from exc
        # A new transcript: read it from its first line (its first turn may end very soon).
        self._d.watcher.watch(conversation_id, title.strip(), from_start=True)
        return _json({"id": conversation_id, "title": title.strip()})

    def _set_model(self, args: JsonObject) -> str:
        conversation_id = str(args["id"])
        if not self._d.owned.is_owned(conversation_id):
            raise ToolError("not possible: only conversations VoiceMate started can change model")
        try:
            self._d.owned.set_model(conversation_id, str(args["model"]))
        except OwnedError as exc:
            raise ToolError(str(exc)) from exc
        return "done"

    def _stop(self, args: JsonObject) -> str:
        conversation_id = str(args["id"])
        if not self._d.owned.is_owned(conversation_id):
            raise ToolError("not possible: only conversations VoiceMate started can be stopped")
        self._d.watcher.unwatch(conversation_id)
        self._d.owned.stop(conversation_id)
        return "stopped"


def _clip(text: str, limit: int = MESSAGE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."
