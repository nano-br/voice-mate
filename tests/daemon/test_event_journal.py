"""EventJournal: ordered ring buffer with long-poll reads (the engine of GET /events)."""

from __future__ import annotations

import threading
import time

from app.daemon.journal import EventJournal


def test_entries_get_monotonic_seqs_and_reads_return_what_came_after() -> None:
    journal = EventJournal(capacity=8)
    for n in range(3):
        journal.append("health", {"n": n})

    read = journal.read(since=1)
    assert [e.seq for e in read.entries] == [2, 3]
    assert [e.data["n"] for e in read.entries] == [1, 2]
    assert read.cursor == 3
    assert read.stale is False
    assert journal.cursor == 3


def test_read_without_news_returns_the_same_cursor_after_the_wait() -> None:
    journal = EventJournal()
    journal.append("health", {})
    started = time.monotonic()
    read = journal.read(since=1, wait=0.2)
    elapsed = time.monotonic() - started
    assert read.entries == []
    assert read.cursor == 1
    assert 0.15 <= elapsed < 2.0


def test_long_poll_returns_as_soon_as_an_entry_arrives() -> None:
    journal = EventJournal()
    threading.Timer(0.15, lambda: journal.append("state", {"x": 1})).start()
    started = time.monotonic()
    read = journal.read(since=0, wait=10.0)
    elapsed = time.monotonic() - started
    assert [e.type for e in read.entries] == ["state"]
    assert elapsed < 2.0  # woke on the append, not at the end of the wait


def test_close_wakes_pending_readers_and_later_reads_do_not_wait() -> None:
    journal = EventJournal()
    results: list[float] = []

    def _reader() -> None:
        started = time.monotonic()
        journal.read(since=0, wait=10.0)
        results.append(time.monotonic() - started)

    thread = threading.Thread(target=_reader)
    thread.start()
    time.sleep(0.1)
    journal.close()
    thread.join(timeout=5.0)
    assert results and results[0] < 2.0
    started = time.monotonic()
    assert journal.read(since=0, wait=10.0).entries == []
    assert time.monotonic() - started < 1.0
    assert journal.closed


def test_cursor_older_than_the_buffer_is_stale() -> None:
    journal = EventJournal(capacity=3)
    for _n in range(5):  # seqs 1..5, buffer keeps 3..5
        journal.append("health", {})
    assert journal.read(since=2).stale is False  # 2 is just before the oldest kept: nothing lost
    assert [e.seq for e in journal.read(since=2).entries] == [3, 4, 5]
    stale = journal.read(since=1, wait=5.0)  # seq 2 was dropped: the reader missed it
    assert stale.stale is True
    assert stale.cursor == 5


def test_cursor_from_the_future_or_negative_is_stale() -> None:
    journal = EventJournal()
    journal.append("health", {})
    assert journal.read(since=7).stale is True  # e.g. a cursor from another daemon instance
    assert journal.read(since=-1).stale is True


def test_empty_journal_reads_from_zero() -> None:
    journal = EventJournal()
    read = journal.read(since=0)
    assert (read.entries, read.cursor, read.stale) == ([], 0, False)


def test_entry_keeps_the_lease_holders_of_its_publication() -> None:
    journal = EventJournal()
    entry = journal.append("result", {}, cue_holder="a", clipboard_holder="b")
    assert (entry.cue_holder, entry.clipboard_holder) == ("a", "b")
    assert entry.ts > 0
