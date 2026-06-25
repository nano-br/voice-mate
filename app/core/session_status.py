"""Session event hub: live state + last result, per consumer.

On WSL2 the trigger comes from outside (the Windows hotkey script hits the daemon
over HTTP). The old protocol was *fire-and-forget* and *toggle*: `/trigger`
responded without saying what it did, and any lost/duplicated event silently
desynchronizes the two sides (the user presses meaning to record and WSL stops).
This hub fixes that at the root — it carries STATE:

  - consumer registration (`register()` → `client_id` generated on WSL);
  - the CURRENT operation (idle/recording/processing) with its `op_seq` and who
    started it;
  - the LAST result (text + increasing `seq`) correlated to the operation.

Two query modes, both available from the start:
  - `scope="all"`  — the GLOBAL state/result (the Windows default today: every
    consumer hears everything). The `seq`/`op_seq` is monotonic, so the consumer
    knows whether what it received is NEWER than the last one it kept.
  - `scope="mine"` — only what THIS `client_id` started (the "mine only" Ctrl+C).

Today there is a single audio source (one microphone), so the current operation is
global; the `client_id` just tags who started it. The per-client route already
exists as a foundation for multiple consumers recording in parallel in the future.

Fed from two sides: the `RecordingSession` reports the state transitions; the
`ClipboardWriter` publishes the final text (`record_result`).

Results live in a circular BUFFER (not just the last one): the producer (WSL) can
generate faster than the consumer (Windows) polls — in back-to-back tests, two
results finish close together and, with a single slot, the first was overwritten
before being read (losing ~half of them). With the buffer, the consumer asks
`result(..., since=<seq>)` and DRAINS the next unseen one in order, without losing
the intermediate ones.
"""

from __future__ import annotations

import secrets
import threading
from collections import deque
from dataclasses import dataclass, replace
from typing import Literal

# How many recent results to keep per buffer (global and per client). Generous:
# the consumer polls every 150-300ms, so it only "lags" by a few; 64 covers bursts
# far larger than any realistic back-to-back test.
_RESULT_BUFFER = 64

SessionState = Literal["idle", "recording", "processing"]
Scope = Literal["all", "mine"]
ToggleAction = Literal["started", "stopped", "restarted"]


@dataclass(frozen=True)
class ToggleOutcome:
    """What a `toggle()` did — returned to the trigger immediately (without waiting for STT).

    `started` = idle→recording · `stopped` = recording→processing (transcribing)
    · `restarted` = processing→recording (cancelled and restarted).
    """

    action: ToggleAction
    op_seq: int
    state: SessionState
    flow: str


@dataclass(frozen=True)
class Operation:
    """Snapshot of the current operation (or of a consumer's last one)."""

    op_seq: int
    state: SessionState
    flow: str | None
    client_id: str | None


@dataclass(frozen=True)
class Result:
    """Last text produced, correlated to the operation that generated it."""

    seq: int
    text: str
    op_seq: int
    client_id: str | None


_IDLE = Operation(op_seq=0, state="idle", flow=None, client_id=None)
_EMPTY = Result(seq=0, text="", op_seq=0, client_id=None)


class SessionStatus:
    """Live state + last session result, queryable per consumer.

    Thread-safe. The `RecordingSession` is the authority for `op_seq` (passes it
    in the `set_operation` calls); the store just keeps snapshots and correlates
    the result with the current operation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: set[str] = set()
        self._result_seq = 0
        self._current = _IDLE
        self._last_result = _EMPTY
        # Circular buffer of recent results (global), so the consumer can
        # drain them in order via `result(since=...)` without losing intermediate ones.
        self._results: deque[Result] = deque(maxlen=_RESULT_BUFFER)
        # Per consumer: the last operation IT started and the buffer of its
        # results (the scope="mine" mode).
        self._client_op: dict[str, Operation] = {}
        self._client_result: dict[str, Result] = {}
        self._client_results: dict[str, deque[Result]] = {}

    # -- consumer registration ---------------------------------------------
    def register(self) -> str:
        """Generate and register a new `client_id` (short uuid, generated on WSL)."""
        client_id = secrets.token_hex(8)
        with self._lock:
            self._clients.add(client_id)
        return client_id

    def is_registered(self, client_id: str) -> bool:
        with self._lock:
            return client_id in self._clients

    # -- state transitions (called by RecordingSession) --------------------
    def set_operation(self, op_seq: int, state: SessionState, flow: str | None, client_id: str | None) -> None:
        """Update the current operation. `op_seq` new (start) or the same (stop)."""
        op = Operation(op_seq=op_seq, state=state, flow=flow, client_id=client_id)
        with self._lock:
            self._current = op
            if client_id is not None:
                self._client_op[client_id] = op

    def mark_idle(self, op_seq: int) -> None:
        """Return the current operation to idle if it is still `op_seq` (race-free)."""
        with self._lock:
            if self._current.op_seq == op_seq:
                self._current = replace(self._current, state="idle")
                cid = self._current.client_id
                if cid is not None and cid in self._client_op:
                    self._client_op[cid] = replace(self._client_op[cid], state="idle")

    # -- result (called by ClipboardWriter on copy) ------------------------
    def record_result(self, text: str) -> None:
        """Publish the final text, correlating it to the CURRENT operation."""
        with self._lock:
            self._result_seq += 1
            op = self._current
            result = Result(seq=self._result_seq, text=text, op_seq=op.op_seq, client_id=op.client_id)
            self._last_result = result
            self._results.append(result)
            if op.client_id is not None:
                self._client_result[op.client_id] = result
                self._client_results.setdefault(op.client_id, deque(maxlen=_RESULT_BUFFER)).append(result)

    # -- queries -----------------------------------------------------------
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
                "scope": scope,
            }

    def result(self, client_id: str | None, scope: Scope, since: int | None = None) -> dict[str, object]:
        """Last result — or, with `since`, the NEXT unseen one (seq > since).

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
                "scope": scope,
            }

    # -- compat: the old global (seq, text) API ----------------------------
    def get(self) -> tuple[int, str]:
        with self._lock:
            return self._last_result.seq, self._last_result.text
