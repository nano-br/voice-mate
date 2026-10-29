"""Mode "desktop": the central conversation is a Claude Desktop conversation VoiceMate does not own.

`DesktopConversationPort` implements `ConversationPort` without a child process. Writing is a
"flash": the Desktop window is brought forward, the text is pasted into the prompt of the conversation
called exactly `title` and Enter is pressed (`app.companion.win.claude_desktop`, injected as a
`ConversationSender`). Reading is the usual transcript tail: the service follows the session id this
port reports, and this port reads the same file to know whether the conversation is busy or idle
(docs/research/claude-code-client.md, sections 5 to 7).

The first message after `start` is `/voicemate-assistant` (the person may have run it already: it is
sent once per VoiceMate start). The Desktop serves its own session tools to that conversation, so the
`voicemate` MCP tools do not exist here. One worker thread does everything in order (sends take about
2 s and must not overlap) and, between jobs, polls the transcript. `send` only queues the text and
returns at once, so the caller (the assistant's worker) never waits for a flash; a flash that fails
puts the port in error and hands the text to `on_failed`. Standard library only (no Qt).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Final

from app.companion.assistant.contract import PortListener, PortStatus
from app.companion.assistant.own_session import FIRST_PROMPT
from app.companion.assistant.session_reader import (
    ConversationInfo,
    LiveSession,
    ReaderEvent,
    Tail,
    find_sessions_by_title,
    live_sessions,
)
from app.companion.win.claude_desktop import ConversationSender, DesktopError

log = logging.getLogger(__name__)

POLL_S: Final = 1.0
STOP_WAIT_S: Final = 8.0
AWAIT_ECHO_S: Final = 20.0  # after a send, how long "busy" holds while the prompt has not shown in the file


class DesktopConversationPort:
    """A `ConversationPort` whose conversation is one in the Claude Desktop, found by its title."""

    def __init__(
        self,
        title: str,
        sender: ConversationSender,
        *,
        send_first_prompt: bool = True,
        find_sessions: Callable[[str], list[ConversationInfo]] = find_sessions_by_title,
        sessions: Callable[[], dict[str, LiveSession]] = live_sessions,
        tail_factory: Callable[[Path], Tail] = Tail,
        poll_s: float = POLL_S,
        monotonic: Callable[[], float] = time.monotonic,
        on_failed: Callable[[str], None] | None = None,
    ) -> None:
        self._title = title.strip()
        self._sender = sender
        self._send_first_prompt = send_first_prompt
        self._find_sessions = find_sessions
        self._sessions = sessions
        self._tail_factory = tail_factory
        self._poll_s = poll_s
        self._monotonic = monotonic
        self._on_failed = on_failed  # (text) a queued send whose flash failed (worker thread)
        self._lock = threading.RLock()
        self._status = PortStatus()
        self._listener: PortListener | None = None
        self._jobs: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = False
        # Worker-owned.
        self._tail: Tail | None = None
        self._turn_open = False
        self._awaiting_since: float | None = None

    # --- ConversationPort --------------------------------------------------------------------

    def status(self) -> PortStatus:
        with self._lock:
            return self._status

    def start(self, on_change: PortListener) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._listener = on_change
            self._stopping = False
            self._status = PortStatus(state="starting")
            self._thread = threading.Thread(target=self._run, name="desktop-port", daemon=True)
            thread = self._thread
        self._notify()
        thread.start()

    def send(self, text: str) -> bool:
        """Queue `text` for the next flash and return at once: True when queued. A flash that
        fails later reports through the status (error) and `on_failed`."""
        with self._lock:
            if self._thread is None or self._stopping or self._status.state not in ("starting", "ready", "busy"):
                return False
            self._jobs.put(text)
        return True

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            self._stopping = True
        if thread is None:
            return
        self._jobs.put(None)
        if thread is not threading.current_thread():
            thread.join(STOP_WAIT_S)
        with self._lock:
            self._thread = None
            session_id = self._status.session_id
        self._drop_pending(thread)
        self._set(PortStatus(state="stopped", session_id=session_id))

    def _drop_pending(self, thread: threading.Thread) -> None:
        """Stopped (a quit or a switch): what was still queued is not sent, and the caller hears of
        each text through `on_failed`; the end marker goes back only for a worker still inside a flash."""
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            if job is not None:
                self._failed(job)
        if thread.is_alive():
            self._jobs.put(None)

    # --- worker ------------------------------------------------------------------------------

    def _run(self) -> None:
        if not self._attach():
            self._drain()
            return
        if self._send_first_prompt:
            self._deliver(FIRST_PROMPT)
        else:
            self._refresh()
        while True:
            try:
                job = self._jobs.get(timeout=self._poll_s)
            except queue.Empty:
                self._refresh()
                continue
            if job is None:
                return
            if not self._deliver(job):
                self._failed(job)

    def _failed(self, text: str) -> None:
        if self._on_failed is None:
            return
        try:
            self._on_failed(text)
        except Exception:  # noqa: BLE001 - a callback must not kill the worker thread
            log.exception("desktop port: on_failed callback failed")

    def _drain(self) -> None:
        """After a failed start: the sends that were already queued failed too, until stopped."""
        while True:
            job = self._jobs.get()
            if job is None:
                return
            self._failed(job)

    def _attach(self) -> bool:
        """Find the transcript of the conversation by its custom title and follow it from its end."""
        if not self._title:
            self._set(PortStatus(state="error", error="setup_failed", detail="no Desktop conversation is configured"))
            return False
        found = self._find_sessions(self._title)
        if not found:
            self._set(
                PortStatus(
                    state="error",
                    error="setup_failed",
                    detail=f"no Claude Desktop conversation titled {self._title!r} has a transcript",
                )
            )
            return False
        if len(found) > 1:
            log.info("desktop port: %d transcripts are titled %r, following the newest", len(found), self._title)
        info = found[0]
        self._tail = self._tail_factory(info.path)
        self._set(PortStatus(state="starting", session_id=info.session_id))
        return True

    def _deliver(self, text: str) -> bool:
        try:
            self._sender.send(self._title, text)
        except DesktopError as exc:
            log.warning("desktop port: flash to %r failed (%s): %s", self._title, exc.code, exc.detail)
            self._fail(f"{exc.code}: {exc.detail}")
            return False
        except Exception as exc:  # noqa: BLE001 - UI Automation can raise anything (COMError, OSError)
            log.exception("desktop port: flash to %r crashed", self._title)
            self._fail(f"unexpected: {exc}")
            return False
        self._awaiting_since = self._monotonic()  # busy until the prompt shows in the transcript
        self._refresh()
        return True

    def _fail(self, detail: str) -> None:
        with self._lock:
            session_id = self._status.session_id
        self._set(PortStatus(state="error", error="start_failed", detail=detail, session_id=session_id))

    def _refresh(self) -> None:
        """Read what was appended to the transcript and publish busy or ready."""
        tail = self._tail
        if tail is None:
            return
        for event in tail.poll():
            self._on_event(event)
        live = self._sessions().get(self._status.session_id)
        live_busy = live is not None and live.status == "busy"
        waiting = self._awaiting_since is not None and self._monotonic() - self._awaiting_since < AWAIT_ECHO_S
        with self._lock:
            if self._status.state in ("error", "stopped"):
                return
            busy = self._turn_open or live_busy or waiting
            if busy and self._status.state != "busy":
                self._status = replace(self._status, state="busy")
            elif not busy and self._status.state != "ready":
                self._status = replace(self._status, state="ready")
            else:
                return
        self._notify()

    def _on_event(self, event: ReaderEvent) -> None:
        if event.kind in ("user", "assistant_text"):
            self._turn_open = True
            self._awaiting_since = None
        elif event.kind == "turn_end":
            self._turn_open = False
            self._awaiting_since = None

    # --- status ------------------------------------------------------------------------------

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
            except Exception:  # noqa: BLE001 - a listener must not kill the worker thread
                log.exception("desktop port: status listener failed")
