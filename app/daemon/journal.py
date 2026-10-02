"""Event journal: the ordered stream behind `GET /events` (ring buffer + long poll).

Every entry gets a monotonic `seq`. A reader asks for what came after its
cursor and, when nothing did, waits on a `threading.Condition` until an entry
is appended, the wait runs out, or the journal is closed (daemon shutdown).
The buffer keeps the last `capacity` entries: a cursor older than that (or one
from the future, i.e. from another daemon instance) is reported as stale and
the client starts over from a snapshot.

Entries are stored once for everyone. Fields that depend on WHO reads them
(`needs_cue`, `needs_delivery`) are rendered per client from the lease holders
recorded at publish time.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.protocol.models import EventType

DEFAULT_CAPACITY = 512


@dataclass(frozen=True)
class JournalEntry:
    seq: int
    ts: float  # daemon wall clock
    type: EventType
    data: Mapping[str, object]
    # Holders of the `cues` / `clipboard` leases when the entry was published.
    cue_holder: str | None = None
    clipboard_holder: str | None = None


@dataclass(frozen=True)
class JournalRead:
    entries: list[JournalEntry]
    cursor: int  # seq of the last entry returned (or the unchanged cursor)
    stale: bool  # the cursor is outside the buffer: the reader must reset from a snapshot


class EventJournal:
    """Thread-safe ring buffer of events with blocking reads."""

    def __init__(
        self,
        capacity: int = DEFAULT_CAPACITY,
        wall: Callable[[], float] = time.time,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._entries: deque[JournalEntry] = deque(maxlen=capacity)
        self._cond = threading.Condition()
        self._wall = wall
        self._clock = clock
        self._seq = 0
        self._closed = False

    @property
    def cursor(self) -> int:
        """Seq of the newest entry (0 while empty)."""
        with self._cond:
            return self._seq

    @property
    def closed(self) -> bool:
        with self._cond:
            return self._closed

    def append(
        self,
        event_type: EventType,
        data: Mapping[str, object],
        *,
        cue_holder: str | None = None,
        clipboard_holder: str | None = None,
    ) -> JournalEntry:
        with self._cond:
            self._seq += 1
            entry = JournalEntry(
                seq=self._seq,
                ts=self._wall(),
                type=event_type,
                data=data,
                cue_holder=cue_holder,
                clipboard_holder=clipboard_holder,
            )
            self._entries.append(entry)
            self._cond.notify_all()
        return entry

    def read(self, since: int, wait: float = 0.0) -> JournalRead:
        """Entries with `seq > since`; blocks up to `wait` seconds while there are none.

        Returns at once when the cursor is stale, when entries are available, or
        when the journal is closed (a closed journal never makes anyone wait).
        """
        deadline = self._clock() + max(0.0, wait)
        with self._cond:
            while True:
                if self._is_stale_locked(since):
                    return JournalRead(entries=[], cursor=self._seq, stale=True)
                entries = self._after_locked(since)
                remaining = deadline - self._clock()
                if entries or self._closed or remaining <= 0:
                    cursor = entries[-1].seq if entries else since
                    return JournalRead(entries=entries, cursor=cursor, stale=False)
                self._cond.wait(remaining)

    def close(self) -> None:
        """Wake every pending reader and stop future reads from waiting."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def _is_stale_locked(self, since: int) -> bool:
        if since < 0 or since > self._seq:
            return True
        first = self._entries[0].seq if self._entries else self._seq + 1
        return since < first - 1

    def _after_locked(self, since: int) -> list[JournalEntry]:
        if not self._entries or since >= self._seq:
            return []
        # Seqs are contiguous inside the buffer, so the start index is arithmetic.
        start = since - self._entries[0].seq + 1
        return list(itertools.islice(self._entries, start, None))
