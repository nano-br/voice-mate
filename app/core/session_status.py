"""Session event hub: live state + results, for the v1 consumers and the API v2.

On WSL2 the trigger comes from outside (the Windows hotkey script, or the
companion app, hits the daemon over HTTP). The old protocol was
*fire-and-forget* and *toggle*: `/trigger` responded without saying what it
did, and any lost/duplicated event silently desynchronizes the two sides (the
user presses meaning to record and WSL stops). This hub fixes that at the root:
it carries STATE.

Two audiences, fed by the same producer calls:

  - v1 (`/status`, `/result`, the Windows scripts): consumer registration, the
    CURRENT operation (idle/recording/processing) with its `op_seq` and who
    started it, and a circular buffer of results that the consumer drains in
    order with `result(since=...)`. Two query modes: `scope="all"` (the global
    state/result; the monotonic `seq`/`op_seq` tells whether it is newer than
    the last one kept) and `scope="mine"` (only what THIS `client_id` started).
    Errors travel in the same ordered stream (`text` empty, `error` set).
  - API v2 (`app.daemon.hub.EventHub`, the companion): typed events with
    phases, mic_live, results with delivery tracking, leases and cue decisions.

The `RecordingSession` reports the state transitions and failures; the handlers
publish their results through the `OperationHandle` of the operation they serve.
Results live in a circular BUFFER (not just the last one): the producer (WSL)
can generate faster than a v1 consumer (Windows) polls.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, replace
from typing import Literal

from app.daemon.hub import DAEMON_ONLY, EventHub, Publication
from app.protocol.models import (
    CancelAction,
    ErrorCode,
    FlowKind,
    Phase,
    ResultKind,
    SessionState,
    TriggerAction,
    WarningCode,
)

__all__ = [
    "CancelOutcome",
    "ErrorCode",
    "OperationHandle",
    "Publication",
    "Scope",
    "SessionState",
    "SessionStatus",
    "ToggleOutcome",
]

# How many recent results to keep per v1 buffer (global and per client). Generous:
# the consumer polls every 150-300ms, so it only "lags" by a few; 64 covers bursts
# far larger than any realistic back-to-back test.
_RESULT_BUFFER = 64

Scope = Literal["all", "mine"]


@dataclass(frozen=True)
class ToggleOutcome:
    """What a trigger did, returned at once (without waiting for the STT).

    `started` = idle -> recording; `stopped` = recording -> processing
    (transcribing); `restarted` = processing -> recording (the old operation's
    pending work is cancelled); `noop` = the `expect` of the trigger did not
    match the state (e.g. `start` while already recording).
    """

    action: TriggerAction
    op_seq: int
    state: SessionState
    flow: str


@dataclass(frozen=True)
class CancelOutcome:
    action: CancelAction
    state: SessionState


@dataclass(frozen=True)
class Operation:
    """Snapshot of the current operation (or of a consumer's last one), v1 view."""

    op_seq: int
    state: SessionState
    flow: str | None
    client_id: str | None


@dataclass(frozen=True)
class Result:
    """A published text (or error event), correlated to the operation that produced it.

    An error event is a Result with empty `text`, an `error` code and a
    human-readable (localized) `message`.
    """

    seq: int
    text: str
    op_seq: int
    client_id: str | None
    error: ErrorCode | None = None
    message: str = ""


_IDLE = Operation(op_seq=0, state="idle", flow=None, client_id=None)
_EMPTY = Result(seq=0, text="", op_seq=0, client_id=None)


class SessionStatus:
    """Live state + session results, queryable per consumer (v1) and as events (v2).

    Thread-safe. The `RecordingSession` is the authority for `op_seq` (it passes
    it in every call); the store keeps snapshots and correlates the results with
    the operation that produced them.
    """

    def __init__(self, hub: EventHub | None = None) -> None:
        self.hub = hub if hub is not None else EventHub()
        # Random per daemon process: `seq`/`op_seq` restart at 0 when the daemon
        # restarts, so a consumer that sees a new `instance` must drain from 0 again.
        self.instance = self.hub.instance
        self._lock = threading.Lock()
        self._result_seq = 0
        self._current = _IDLE
        self._last_result = _EMPTY
        self._results: deque[Result] = deque(maxlen=_RESULT_BUFFER)
        # Per consumer: the last operation IT started and the buffer of its
        # results (the scope="mine" mode).
        self._client_op: dict[str, Operation] = {}
        self._client_result: dict[str, Result] = {}
        self._client_results: dict[str, deque[Result]] = {}

    # -- consumer registration ---------------------------------------------
    def register(self) -> str:
        """Generate and register a new `client_id` (v1: no leases)."""
        return self.hub.register_plain()

    def is_registered(self, client_id: str) -> bool:
        return self.hub.is_known(client_id)

    # -- state transitions (called by RecordingSession) --------------------
    def set_operation(
        self,
        op_seq: int,
        state: SessionState,
        flow: str | None,
        client_id: str | None,
        *,
        flow_kind: FlowKind | None = None,
        phase: Phase | None = None,
    ) -> None:
        """Update the current operation. `op_seq` new (start) or the same (stop)."""
        op = Operation(op_seq=op_seq, state=state, flow=flow, client_id=client_id)
        with self._lock:
            self._current = op
            if client_id is not None:
                self._client_op[client_id] = op
        self.hub.set_state(op_seq, state, flow=flow, flow_kind=flow_kind, client_id=client_id, phase=phase)

    def mark_idle(self, op_seq: int) -> None:
        """Return the current operation to idle if it is still `op_seq` (race-free)."""
        with self._lock:
            if self._current.op_seq == op_seq:
                self._current = replace(self._current, state="idle")
                cid = self._current.client_id
                if cid is not None and cid in self._client_op:
                    self._client_op[cid] = replace(self._client_op[cid], state="idle")
        self.hub.set_idle(op_seq)

    def set_mic_live(self, op_seq: int) -> bool:
        """The microphone of `op_seq` is capturing. True = the daemon plays the start beep."""
        return self.hub.set_mic_live(op_seq)

    def set_phase(self, op_seq: int, phase: Phase) -> None:
        self.hub.set_phase(op_seq, phase)

    def cancel_operation(self, op_seq: int) -> None:
        """Nothing more is published for `op_seq` (no result for a cancelled operation)."""
        self.hub.cancel(op_seq)

    def is_cancelled(self, op_seq: int) -> bool:
        return self.hub.is_cancelled(op_seq)

    # -- results and events (called through OperationHandle) ----------------
    def publish_result(
        self,
        text: str,
        *,
        op_seq: int,
        client_id: str | None,
        kind: ResultKind,
        flow: str | None,
        final: bool,
        spoken: bool = False,
    ) -> Publication:
        """Publish a text produced by `op_seq`: an API v2 event plus the v1 result stream.

        The returned `Publication` says whether the daemon must write the clipboard
        and play the cue itself (nobody holds those leases).
        """
        publication = self.hub.publish_result(text, op_seq=op_seq, kind=kind, flow=flow, final=final, spoken=spoken)
        if publication.published:
            self._append(text, None, "", Operation(op_seq=op_seq, state="idle", flow=flow, client_id=client_id))
        return publication

    def publish_error(self, code: ErrorCode, *, detail: str, message: str, op_seq: int, client_id: str | None) -> bool:
        """Publish an error event of `op_seq`. True = the daemon plays the error beep itself.

        Tagged explicitly (not with the current operation): the failure arrives
        asynchronously and a newer operation may already have started.
        """
        publication = self.hub.publish_error(code, detail=detail, message=message, op_seq=op_seq)
        if publication.published:
            op = Operation(op_seq=op_seq, state="idle", flow=None, client_id=client_id)
            self._append("", code, message, op)
        return publication.play_cue

    def publish_warning(self, code: WarningCode, *, detail: str, message: str, op_seq: int) -> bool:
        """Publish a warning event (API v2 only). True = the daemon plays the warning beep itself."""
        return self.hub.publish_warning(code, detail=detail, message=message, op_seq=op_seq).play_cue

    def record_result(self, text: str) -> None:
        """Publish a final transcript for the CURRENT operation (shortcut for tests/tools)."""
        with self._lock:
            op = self._current
        self.publish_result(text, op_seq=op.op_seq, client_id=op.client_id, kind="transcript", flow=op.flow, final=True)

    def record_error(self, code: ErrorCode, message: str, op_seq: int, client_id: str | None) -> None:
        """Publish an error event for the operation that failed (v1 signature)."""
        self.publish_error(code, detail="", message=message, op_seq=op_seq, client_id=client_id)

    def operation(
        self, op_seq: int, flow: str | None, client_id: str | None, superseded: threading.Event | None = None
    ) -> OperationHandle:
        return OperationHandle(self, op_seq=op_seq, flow=flow, client_id=client_id, superseded=superseded)

    def _append(self, text: str, error: ErrorCode | None, message: str, op: Operation) -> None:
        with self._lock:
            self._result_seq += 1
            result = Result(
                seq=self._result_seq,
                text=text,
                op_seq=op.op_seq,
                client_id=op.client_id,
                error=error,
                message=message,
            )
            self._last_result = result
            self._results.append(result)
            if op.client_id is not None:
                self._client_result[op.client_id] = result
                self._client_results.setdefault(op.client_id, deque(maxlen=_RESULT_BUFFER)).append(result)

    # -- v1 queries ----------------------------------------------------------
    def status(self, client_id: str | None, scope: Scope) -> dict[str, object]:
        with self._lock:
            op = self._client_op.get(client_id, _IDLE) if scope == "mine" and client_id else self._current
            return {
                "state": op.state,
                "op_seq": op.op_seq,
                "flow": op.flow,
                "client_id": op.client_id,
                "is_yours": client_id is not None and op.client_id == client_id,
                "result_seq": self._last_result.seq,
                "instance": self.instance,
                "scope": scope,
            }

    def result(self, client_id: str | None, scope: Scope, since: int | None = None) -> dict[str, object]:
        """Last result, or, with `since`, the NEXT unseen one (seq > since).

        The consumer drains in order: it asks `since=<last seq I handled>` and gets
        the result with the smallest seq not yet seen. When there is nothing new
        left, it returns the last one (whose seq is <= since), so the consumer stops draining.
        """
        with self._lock:
            if scope == "mine" and client_id:
                latest = self._client_result.get(client_id, _EMPTY)
                buffer: deque[Result] | None = self._client_results.get(client_id)
            else:
                latest = self._last_result
                buffer = self._results
            res = latest
            if since is not None and buffer:
                res = min((r for r in buffer if r.seq > since), key=lambda r: r.seq, default=latest)
            return {
                "seq": res.seq,
                "text": res.text,
                "op_seq": res.op_seq,
                "client_id": res.client_id,
                "error": res.error,
                "message": res.message,
                "instance": self.instance,
                "scope": scope,
            }

    # -- compat: the old global (seq, text) API ----------------------------
    def get(self) -> tuple[int, str]:
        with self._lock:
            return self._last_result.seq, self._last_result.text


class OperationHandle:
    """One operation as seen by the handler that serves it (bound to its `op_seq`).

    Handlers publish through it instead of writing the clipboard and beeping on
    their own: the `Publication` says what the daemon must still do itself (a
    client may hold the clipboard or cues lease). A handle without a status
    (`OperationHandle()`) is detached: every job stays with the daemon.

    `superseded` is set when a new recording took over (a restart while
    processing) or the operation was cancelled: the handler must not START new
    work (e.g. call Claude), though what was already produced may still be
    published. A cancelled operation publishes nothing (the hub drops it).
    """

    def __init__(
        self,
        status: SessionStatus | None = None,
        *,
        op_seq: int = 0,
        flow: str | None = None,
        client_id: str | None = None,
        superseded: threading.Event | None = None,
    ) -> None:
        self._status = status
        self.op_seq = op_seq
        self.flow = flow
        self.client_id = client_id
        self._superseded = superseded if superseded is not None else threading.Event()

    def cancelled(self) -> bool:
        return self._status is not None and self._status.is_cancelled(self.op_seq)

    def active(self) -> bool:
        """False once a new recording took over or the operation was cancelled."""
        return not self._superseded.is_set() and not self.cancelled()

    def set_phase(self, phase: Phase) -> None:
        if self._status is not None:
            self._status.set_phase(self.op_seq, phase)

    def publish_result(self, text: str, kind: ResultKind, *, final: bool, spoken: bool = False) -> Publication:
        if self._status is None:
            return DAEMON_ONLY
        return self._status.publish_result(
            text,
            op_seq=self.op_seq,
            client_id=self.client_id,
            kind=kind,
            flow=self.flow,
            final=final,
            spoken=spoken,
        )

    def publish_error(self, code: ErrorCode, *, detail: str, message: str) -> bool:
        """Report a failure of this operation. True = the daemon plays the error beep itself.

        Nothing is reported once the operation was superseded or cancelled (an
        interrupted call is not an error the user needs to hear about).
        """
        if not self.active():
            return False
        if self._status is None:
            return True
        return self._status.publish_error(
            code, detail=detail, message=message, op_seq=self.op_seq, client_id=self.client_id
        )
