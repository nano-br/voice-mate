from __future__ import annotations

import pytest

from app.companion.contract import RecentItem
from app.companion.delivery import (
    IN_FLIGHT_TIMEOUT_S,
    PENDING_LIMIT,
    RECENT_LIMIT,
    Delivered,
    DeliveryEffect,
    DeliveryFailed,
    DeliveryQueue,
    NotCopied,
    SendAck,
    StartDelivery,
)
from app.protocol.models import ResultData, ResultRecord

INSTANCE = "inst-a"


def result(seq: int, text: str = "", *, needs_delivery: bool = True, final: bool = True) -> ResultData:
    return ResultData(
        result_seq=seq,
        op_seq=seq,
        kind="transcript",
        flow="clipboard",
        text=text or f"text {seq}",
        needs_delivery=needs_delivery,
        final=final,
        spoken=False,
        needs_cue=True,
    )


def record(seq: int, age_s: float, delivery: str = "pending") -> ResultRecord:
    return ResultRecord(
        result_seq=seq,
        op_seq=seq,
        kind="transcript",
        flow="clipboard",
        text=f"old {seq}",
        final=True,
        created_ts=1000.0,
        age_s=age_s,
        delivery=delivery,  # type: ignore[typeddict-item]
    )


def started(effects: list[DeliveryEffect]) -> list[int]:
    return [e.job.result_seq for e in effects if isinstance(e, StartDelivery)]


def acks(effects: list[DeliveryEffect]) -> list[tuple[int, str]]:
    return [(a["result_seq"], a["status"]) for e in effects if isinstance(e, SendAck) for a in e.acks]


def queue() -> DeliveryQueue:
    q = DeliveryQueue()
    q.set_instance(INSTANCE, 0.0)
    return q


def test_delivers_in_order_one_at_a_time_and_acks() -> None:
    q = queue()
    assert started(q.on_result(INSTANCE, result(1), 0.0, 0.0)) == [1]
    assert started(q.on_result(INSTANCE, result(2), 0.0, 0.0)) == []  # 1 is in flight
    effects = q.on_delivery_done((INSTANCE, 1), True, 0.1)
    assert acks(effects) == [(1, "delivered")]
    assert any(isinstance(e, Delivered) and e.job.result_seq == 1 for e in effects)
    assert started(effects) == [2]
    effects = q.on_delivery_done((INSTANCE, 2), True, 0.2)
    assert acks(effects) == [(2, "delivered")]
    assert [item.record["delivery"] for item in q.recent()] == ["delivered", "delivered"]


def test_results_without_delivery_only_go_to_recent() -> None:
    q = queue()
    assert q.on_result(INSTANCE, result(1, needs_delivery=False), 5.0, 0.0) == []
    (item,) = q.recent()
    assert item.record["delivery"] == "daemon"
    assert item.record["created_ts"] == 5.0


def test_never_sets_the_same_result_twice() -> None:
    q = queue()
    q.on_result(INSTANCE, result(1), 0.0, 0.0)
    assert q.on_result(INSTANCE, result(1), 0.0, 0.0) == []
    assert started(q.on_unacked(INSTANCE, [record(1, 0.5)], 0.0)) == []
    q.on_delivery_done((INSTANCE, 1), True, 0.1)
    assert q.on_unacked(INSTANCE, [record(1, 0.5)], 0.2) == []
    assert q.on_result(INSTANCE, result(1), 0.0, 0.3) == []


def test_failure_blocks_later_results_for_three_retries_then_acks_failed() -> None:
    q = queue()
    q.on_result(INSTANCE, result(1), 0.0, 0.0)
    q.on_result(INSTANCE, result(2), 0.0, 0.0)
    now = 0.0
    for attempt in range(3):
        effects = q.on_delivery_done((INSTANCE, 1), False, now)
        assert started(effects) == []  # waits 1 s, and 2 stays blocked
        assert q.next_wakeup() == now + 1.0
        assert q.tick(now + 0.5) == []
        now += 1.0
        assert started(q.tick(now)) == [1], attempt
    effects = q.on_delivery_done((INSTANCE, 1), False, now)
    assert acks(effects) == [(1, "failed")]
    assert any(isinstance(e, DeliveryFailed) and e.job.result_seq == 1 for e in effects)
    assert started(effects) == [2]
    assert [item.record["result_seq"] for item in q.pending()] == [1]
    assert q.pending()[0].record["delivery"] == "failed"


def test_reconciliation_delivers_fresh_and_dismisses_stale_results() -> None:
    q = queue()
    effects = q.on_unacked(
        INSTANCE, [record(1, 45.0), record(2, 3.0), record(3, 31.0), record(4, 1.0, "delivered")], 0.0
    )
    assert acks(effects) == [(1, "dismissed"), (3, "dismissed")]
    assert [e.count for e in effects if isinstance(e, NotCopied)] == [2]
    assert started(effects) == [2]
    job = next(e.job for e in effects if isinstance(e, StartDelivery))
    assert job.needs_cue is False  # a missed event never produces a cue
    assert [item.record["result_seq"] for item in q.pending()] == [1, 3]
    assert all(item.record["delivery"] == "dismissed" for item in q.pending())


def test_reconciliation_ignores_another_instance() -> None:
    q = queue()
    assert q.on_unacked("other", [record(1, 1.0)], 0.0) == []


def test_manual_copy_acks_delivered_and_leaves_pending() -> None:
    q = queue()
    q.on_unacked(INSTANCE, [record(7, 60.0)], 0.0)
    effects = q.copy(INSTANCE, 7, 1.0)
    (job,) = [e.job for e in effects if isinstance(e, StartDelivery)]
    assert job.manual and job.text == "old 7"
    effects = q.on_delivery_done((INSTANCE, 7), True, 1.1)
    assert acks(effects) == [(7, "delivered")]  # last status wins over the earlier dismissed
    assert q.pending() == []


def test_manual_copy_of_an_old_instance_only_copies() -> None:
    q = queue()
    q.on_unacked(INSTANCE, [record(7, 60.0)], 0.0)
    q.set_instance("inst-b", 1.0)
    q.copy(INSTANCE, 7, 2.0)
    effects = q.on_delivery_done((INSTANCE, 7), True, 2.1)
    assert acks(effects) == []
    assert any(isinstance(e, Delivered) for e in effects)
    assert q.pending() == []


def test_manual_copy_failure_keeps_it_pending_without_retries() -> None:
    q = queue()
    q.on_unacked(INSTANCE, [record(7, 60.0)], 0.0)
    q.copy(INSTANCE, 7, 1.0)
    effects = q.on_delivery_done((INSTANCE, 7), False, 1.1)
    assert [type(e) for e in effects] == [DeliveryFailed]
    assert [item.record["result_seq"] for item in q.pending()] == [7]
    assert q.next_wakeup() is None


def test_manual_copy_goes_before_queued_automatic_deliveries() -> None:
    q = queue()
    q.on_unacked(INSTANCE, [record(1, 60.0)], 0.0)
    q.on_result(INSTANCE, result(2), 0.0, 0.0)  # in flight
    q.on_result(INSTANCE, result(3), 0.0, 0.0)  # queued
    q.copy(INSTANCE, 1, 0.1)
    effects = q.on_delivery_done((INSTANCE, 2), True, 0.2)
    assert started(effects) == [1]


def test_instance_change_moves_queued_results_to_pending() -> None:
    q = queue()
    q.on_result(INSTANCE, result(1), 0.0, 0.0)  # in flight
    q.on_result(INSTANCE, result(2), 0.0, 0.0)
    q.on_result(INSTANCE, result(3), 0.0, 0.0)
    effects = q.set_instance("inst-b", 1.0)
    assert [e.count for e in effects if isinstance(e, NotCopied)] == [2]
    assert [item.record["result_seq"] for item in q.pending()] == [2, 3]
    # The in-flight one finishes, but its instance is gone: no ACK (it would be a 409).
    effects = q.on_delivery_done((INSTANCE, 1), True, 1.1)
    assert acks(effects) == []


def test_a_failed_delivery_of_an_old_instance_is_not_retried_on_the_new_one() -> None:
    q = queue()
    q.on_result(INSTANCE, result(1), 0.0, 0.0)  # in flight
    q.set_instance("inst-b", 0.5)  # the daemon restarted meanwhile
    effects = q.on_delivery_done((INSTANCE, 1), False, 1.0)
    assert [e.count for e in effects if isinstance(e, NotCopied)] == [1]
    assert acks(effects) == [] and started(effects) == []
    assert q.next_wakeup() is None  # no retry scheduled
    assert [item.record["result_seq"] for item in q.pending()] == [1]
    assert q.tick(10.0) == []


def test_an_event_after_reconciliation_does_not_rewrite_recent() -> None:
    q = queue()
    q.on_unacked(INSTANCE, [record(4, 1.0)], 0.0)  # queued by reconciliation: no cue info
    q.on_delivery_done((INSTANCE, 4), True, 0.1)
    assert q.recent()[0].record["delivery"] == "delivered"
    assert q.on_result(INSTANCE, result(4), 0.0, 0.2) == []  # the late event
    assert q.recent()[0].record["delivery"] == "delivered"


def test_an_event_racing_reconciliation_fills_the_cue_flags() -> None:
    q = queue()
    q.on_result(INSTANCE, result(1), 0.0, 0.0)  # in flight, blocks 2
    q.on_unacked(INSTANCE, [record(2, 1.0)], 0.1)  # reconciliation saw 2 before its event
    spoken = result(2)
    spoken["kind"] = "ai_response"
    spoken["spoken"] = True
    assert q.on_result(INSTANCE, spoken, 0.0, 0.2) == []  # already queued: not twice
    effects = q.on_delivery_done((INSTANCE, 1), True, 0.3)
    (job,) = [e.job for e in effects if isinstance(e, StartDelivery)]
    assert job.result_seq == 2 and job.needs_cue and job.spoken
    assert [item.record["delivery"] for item in q.recent() if item.record["result_seq"] == 2] == ["pending"]


def test_lost_clipboard_verdict_times_out_as_a_failure() -> None:
    q = queue()
    q.on_result(INSTANCE, result(1), 0.0, 0.0)
    assert q.next_wakeup() == IN_FLIGHT_TIMEOUT_S
    effects = q.tick(IN_FLIGHT_TIMEOUT_S + 0.1)
    assert started(effects) == []
    assert q.next_wakeup() == pytest.approx(IN_FLIGHT_TIMEOUT_S + 1.1)  # retry scheduled


def test_out_of_order_arrival_is_delivered_in_seq_order() -> None:
    q = queue()
    q.on_result(INSTANCE, result(5), 0.0, 0.0)  # in flight
    q.on_result(INSTANCE, result(7), 0.0, 0.0)
    q.on_unacked(INSTANCE, [record(6, 1.0)], 0.0)
    effects = q.on_delivery_done((INSTANCE, 5), True, 0.1)
    assert started(effects) == [6]


def test_recent_and_pending_are_bounded() -> None:
    q = queue()
    for seq in range(1, 30):
        q.on_result(INSTANCE, result(seq, needs_delivery=False), 0.0, 0.0)
    assert len(q.recent()) == RECENT_LIMIT
    assert q.recent()[0].record["result_seq"] == 29  # newest first
    q.on_unacked(INSTANCE, [record(seq, 99.0) for seq in range(100, 100 + PENDING_LIMIT + 5)], 0.0)
    assert len(q.pending()) == PENDING_LIMIT
    assert q.pending()[0].record["result_seq"] == 105  # oldest dropped first


def restored(seq: int, instance: str = INSTANCE, delivery: str = "failed") -> RecentItem:
    """A pending item read back from pending.json (age_s is not stored)."""
    return RecentItem(instance, record(seq, 0.0, delivery))


def test_restored_pending_items_are_offered_for_a_manual_copy() -> None:
    q = DeliveryQueue()
    q.restore_pending([restored(3, "inst-old"), restored(4, "inst-old", "dismissed"), restored(3, "inst-old")])
    assert [(i.instance, i.record["result_seq"]) for i in q.pending()] == [("inst-old", 3), ("inst-old", 4)]
    assert q.set_instance("inst-new", 0.0) == []  # nothing queued: no "not copied" again
    assert started(q.on_result("inst-new", result(3), 0.0, 0.0)) == [3]  # same seq, new instance: another result
    q.on_delivery_done(("inst-new", 3), True, 0.5)
    effects = q.copy("inst-old", 4, 1.0)
    (job,) = [e.job for e in effects if isinstance(e, StartDelivery)]
    assert job.manual and job.text == "old 4"
    effects = q.on_delivery_done(("inst-old", 4), True, 1.2)
    assert acks(effects) == []  # its instance is gone: copied only
    assert [(i.instance, i.record["result_seq"]) for i in q.pending()] == [("inst-old", 3)]


def test_restored_items_respect_the_pending_limit() -> None:
    q = DeliveryQueue()
    q.restore_pending([restored(seq) for seq in range(PENDING_LIMIT + 3)])
    assert len(q.pending()) == PENDING_LIMIT
    assert q.pending()[0].record["result_seq"] == 3


def test_a_restored_item_the_daemon_still_reports_is_acked_again_not_delivered() -> None:
    q = DeliveryQueue()
    q.restore_pending([restored(7)])  # the old run quit before its ACK went out
    q.set_instance(INSTANCE, 0.0)  # the same daemon is still running
    effects = q.on_unacked(INSTANCE, [record(7, 1.0)], 0.5)  # fresh, but already offered
    assert started(effects) == []
    assert acks(effects) == [(7, "dismissed")]
    assert not any(isinstance(e, NotCopied) for e in effects)  # the user already sees it
    assert [i.record["result_seq"] for i in q.pending()] == [7]
    assert q.on_unacked(INSTANCE, [record(7, 11.0)], 10.0) == []  # only once
