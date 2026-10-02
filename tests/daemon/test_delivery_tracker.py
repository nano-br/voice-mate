"""DeliveryTracker: pending until ACKed (last status wins), daemon-computed ages, queries."""

from __future__ import annotations

from app.daemon.delivery import DeliveryTracker
from app.protocol.models import DeliveryStatus


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _tracker(clock: Clock, wall: Clock | None = None, capacity: int = 200) -> DeliveryTracker:
    return DeliveryTracker(capacity=capacity, clock=clock, wall=wall or Clock(1_700_000_000.0))


def _add(tracker: DeliveryTracker, seq: int, delivery: DeliveryStatus = "pending") -> None:
    tracker.add(
        result_seq=seq, op_seq=seq, kind="transcript", flow="clipboard", text=f"t{seq}", final=True, delivery=delivery
    )


def test_pending_until_acked_and_last_status_wins() -> None:
    tracker = _tracker(Clock(10.0))
    _add(tracker, 1)
    _add(tracker, 2)
    _add(tracker, 3, delivery="daemon")  # nobody held the lease: never pending

    assert [r["result_seq"] for r in tracker.pending()] == [1, 2]
    assert tracker.ack([(1, "delivered")]) == [2]
    assert tracker.ack([(2, "dismissed")]) == []
    # A manual copy later turns the dismissed one into delivered.
    tracker.ack([(2, "delivered")])
    recent = {r["result_seq"]: r["delivery"] for r in tracker.query("recent", 10)}
    assert recent == {1: "delivered", 2: "delivered", 3: "daemon"}


def test_unknown_seqs_in_an_ack_are_ignored() -> None:
    tracker = _tracker(Clock(0.0))
    _add(tracker, 1)
    assert tracker.ack([(99, "failed")]) == [1]


def test_age_is_computed_with_the_daemon_clock() -> None:
    """The client never compares created_ts with its own clock (the WSL VM clock drifts)."""
    clock = Clock(100.0)
    tracker = _tracker(clock, wall=Clock(1_000.0))
    _add(tracker, 1)
    clock.now = 142.5
    (record,) = tracker.query("unacked", 10)
    assert record["age_s"] == 42.5
    assert record["created_ts"] == 1_000.0
    assert record["text"] == "t1"
    assert record["final"] is True


def test_unacked_returns_the_oldest_and_recent_the_newest_both_ascending() -> None:
    tracker = _tracker(Clock(0.0))
    for seq in range(1, 6):
        _add(tracker, seq, delivery="pending" if seq % 2 else "daemon")
    assert [r["result_seq"] for r in tracker.query("unacked", 2)] == [1, 3]
    assert [r["result_seq"] for r in tracker.query("recent", 2)] == [4, 5]
    assert len(tracker.query("recent", 1000)) == 5  # limit clamped, never more than stored


def test_capacity_keeps_the_newest_results() -> None:
    tracker = _tracker(Clock(0.0), capacity=3)
    for seq in range(1, 6):
        _add(tracker, seq)
    assert [r["result_seq"] for r in tracker.pending()] == [3, 4, 5]
