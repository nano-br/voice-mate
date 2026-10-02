"""Delivery tracking of published results (`POST /results/ack`, `GET /results`).

A result published while a client held the clipboard lease is `pending` until
that client ACKs it (`delivered`, `failed` or `dismissed`; the last status
wins, so a manual copy can turn a `dismissed` into `delivered`). Without a
holder the daemon wrote the clipboard itself: `daemon`, never pending.

Ages are computed here, with the daemon's monotonic clock: the client must never
compare `created_ts` with its own clock (the WSL VM clock drifts after the host
sleeps).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final

from app.protocol.models import AckStatus, DeliveryStatus, ResultKind, ResultRecord, ResultsQuery

DEFAULT_CAPACITY: Final = 200
LIMIT_DEFAULT: Final = 10
LIMIT_MAX: Final = 100


@dataclass
class _Entry:
    result_seq: int
    op_seq: int
    kind: ResultKind
    flow: str | None
    text: str
    final: bool
    created_ts: float
    created_at: float  # monotonic
    delivery: DeliveryStatus


class DeliveryTracker:
    """Thread-safe store of the last `capacity` results and their delivery status."""

    def __init__(
        self,
        capacity: int = DEFAULT_CAPACITY,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._entries: deque[_Entry] = deque(maxlen=capacity)
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()

    def add(
        self,
        *,
        result_seq: int,
        op_seq: int,
        kind: ResultKind,
        flow: str | None,
        text: str,
        final: bool,
        delivery: DeliveryStatus,
    ) -> None:
        entry = _Entry(result_seq, op_seq, kind, flow, text, final, self._wall(), self._clock(), delivery)
        with self._lock:
            self._entries.append(entry)

    def ack(self, acks: Iterable[tuple[int, AckStatus]]) -> list[int]:
        """Apply the ACKs (unknown or evicted seqs are ignored); returns the seqs still pending."""
        with self._lock:
            by_seq = {entry.result_seq: entry for entry in self._entries}
            for result_seq, status in acks:
                entry = by_seq.get(result_seq)
                if entry is not None:
                    entry.delivery = status
            return [entry.result_seq for entry in self._entries if entry.delivery == "pending"]

    def pending(self) -> list[ResultRecord]:
        """Every pending result, oldest first (delivery order)."""
        with self._lock:
            now = self._clock()
            return [self._record(e, now) for e in self._entries if e.delivery == "pending"]

    def query(self, which: ResultsQuery, limit: int = LIMIT_DEFAULT) -> list[ResultRecord]:
        """`unacked`: the oldest `limit` pending results; `recent`: the newest `limit` results.

        Both in ascending `result_seq` order.
        """
        limit = max(1, min(LIMIT_MAX, limit))
        with self._lock:
            now = self._clock()
            if which == "unacked":
                chosen = [e for e in self._entries if e.delivery == "pending"][:limit]
            else:
                chosen = list(self._entries)[-limit:]
            return [self._record(e, now) for e in chosen]

    @staticmethod
    def _record(entry: _Entry, now: float) -> ResultRecord:
        return {
            "result_seq": entry.result_seq,
            "op_seq": entry.op_seq,
            "kind": entry.kind,
            "flow": entry.flow,
            "text": entry.text,
            "final": entry.final,
            "created_ts": entry.created_ts,
            "age_s": round(max(0.0, now - entry.created_at), 3),
            "delivery": entry.delivery,
        }
