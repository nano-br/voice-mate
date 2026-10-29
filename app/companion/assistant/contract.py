"""What the assistant service shows to the UI and what it needs from the conversation port.

The service (service.py) publishes immutable `AssistantSnapshot`s from ONE worker thread,
in order, like the companion controller: the UI hops to the Qt thread itself. Standard
library only (no Qt).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

if TYPE_CHECKING:
    from app.companion.contract import AssistantSettings

MAX_MESSAGES: Final = 30  # what the text screen keeps

AssistantState = Literal["off", "starting", "ready", "busy", "error", "stopped"]
# Why the state is "error".
# "no_conversation": mode desktop without a Desktop conversation chosen in the settings.
AssistantErrorCode = Literal[
    "cli_missing", "not_logged_in", "start_failed", "exited", "setup_failed", "no_conversation"
]
# "notice" is a line from VoiceMate itself (a command result), never part of the conversation.
MessageRole = Literal["user", "assistant", "notice"]
RemoteState = Literal["off", "pending", "on", "failed"]

Unsubscribe = Callable[[], None]


@dataclass(frozen=True)
class ChatMessage:
    role: MessageRole
    text: str  # what the screen shows: the display block, else the voice block, else the plain text
    degraded: bool = False  # an assistant reply that broke the template: shown as plain text


@dataclass(frozen=True)
class InboxEntry:
    """An answer of another conversation held for "Hear what's ready" (inbox.py)."""

    conversation_id: str
    title: str
    ready_at: float  # time.time()
    answer: str


@dataclass(frozen=True)
class PendingEntry:
    """A request the assistant sent to another conversation and is waiting on (the reply
    actions `follow_up` and `follow_up_done`): the "Waiting for" list."""

    conversation_id: str
    title: str
    reason: str
    since: float  # time.time()


ProfileSourceName = Literal["override", "remote", "packaged"]


@dataclass(frozen=True)
class ProfileStatus:
    """The Claude Desktop profile in use (app/companion/win/desktop_profile.py)."""

    source: ProfileSourceName = "packaged"
    version: int = 0
    error: str = ""  # English: why an imported or downloaded profile was ignored
    checking: bool = False


@dataclass(frozen=True)
class AssistantSnapshot:
    rev: int = 0
    state: AssistantState = "off"
    error: AssistantErrorCode | None = None
    session_url: str = ""  # the claude.ai/code link of the session, once Remote Control is on
    remote: RemoteState = "off"
    messages: tuple[ChatMessage, ...] = ()
    followed_title: str = ""  # a conversation the screen follows instead of the assistant's own
    direct_title: str = ""  # direct mode: every prompt goes verbatim to this conversation
    inbox: tuple[InboxEntry, ...] = ()
    pending: tuple[PendingEntry, ...] = ()  # what the assistant is waiting for, oldest first
    # Bumped by every request to show the assistant window (its hotkey): the UI presents it.
    window_requests: int = 0
    profile: ProfileStatus | None = None  # None until the Desktop profile is loaded


AssistantListener = Callable[[AssistantSnapshot], None]


class AssistantService(Protocol):
    """Every method returns quickly and may be called from any thread."""

    def start(self) -> None:
        """Start the conversation when the mode is not "off"."""

    def stop(self) -> None:
        """BLOCKING (a few seconds at most): stop the conversation's process. Used at quit."""

    def snapshot(self) -> AssistantSnapshot: ...

    def subscribe(self, listener: AssistantListener) -> Unsubscribe:
        """Delivers the current snapshot at once, then every change (worker thread)."""

    def apply_settings(self, settings: AssistantSettings) -> None:
        """The settings changed: restart the conversation when what it runs with changed."""

    def send(self, text: str) -> None:
        """Typed text: a voice command, or a prompt for the assistant."""

    def restart(self) -> None:
        """Start the conversation again (after an error)."""

    def follow_assistant(self) -> None:
        """Make the screen follow the assistant's own conversation again."""

    def back_to_assistant(self) -> None:
        """Leave direct mode (and tell the assistant what happened there)."""

    def show_window(self) -> None:
        """Ask the UI to show the assistant window (`window_requests` goes up)."""

    def hear_ready(self) -> None:
        """ "Hear what's ready": the held answers go to the assistant in one turn."""

    def clear_inbox(self) -> None: ...

    def refresh_context(self) -> None:
        """Settings outside `[assistant]` changed: rewrite assistant.json (its settings catalog)."""

    def check_profile(self) -> None:
        """ "Check now": ask for a newer Claude Desktop profile (a notice tells the result)."""

    def import_profile(self, path: str) -> None:
        """Use this profile file instead of the published one (a notice tells the result)."""

    def export_profile(self, path: str) -> None:
        """Save the profile in use as a JSON file."""

    def use_default_profile(self) -> None:
        """Forget the imported profile: the published (or packaged) one applies again."""


class VoicePort(Protocol):
    """How the assistant speaks (app/companion/voice.py, `EngineVoice`). Every method returns at once."""

    def speak(self, key: str, text: str, *, final: bool, language: str = "") -> None:
        """The voice block of reply `key` so far (expression tags kept): new whole sentences
        are spoken; `final` speaks the rest and ends the reply."""

    def notice(self, text: str, language: str) -> None:
        """A short fixed line of VoiceMate itself."""

    def stop(self) -> None:
        """Silence now and forget what was queued."""

    def on_unavailable(self, listener: Callable[[str], None]) -> None:
        """`listener(reason)` (localized) once each time the voice stops working."""


@dataclass(frozen=True)
class PortStatus:
    state: Literal["starting", "ready", "busy", "error", "stopped"] = "stopped"
    error: AssistantErrorCode | None = None
    detail: str = ""  # English, for the log
    session_id: str = ""
    session_url: str = ""
    remote: RemoteState = "off"


PortListener = Callable[[PortStatus], None]


class ConversationPort(Protocol):
    """One Claude Code conversation VoiceMate writes to."""

    def start(self, on_change: PortListener) -> None:
        """Spawn the conversation; `on_change` gets every status change (any thread)."""

    def send(self, text: str) -> bool:
        """Write a user prompt; False when there is no running conversation."""

    def status(self) -> PortStatus: ...

    def stop(self) -> None:
        """BLOCKING: close it cleanly, kill it when it does not end."""
