"""Clipboard delivery bookkeeping: ONE queue keyed by (instance, result_seq).

Pure state machine (no I/O, time passed in): the controller feeds it results from
`/events`, unacked records from reconciliation and the clipboard thread's verdicts, and
executes the effects it returns (start a clipboard write, POST an ACK, tell the model).
Rules from docs/companion-app.md, "Delivery, ACK and reconciliation":

- a result is never set twice: events and reconciliation only enqueue what is not
  already queued, in flight or ACKed;
- deliveries go out in `result_seq` order; a failing one blocks the next ones for at most
  RETRIES retries, RETRY_DELAY_S apart, then it is ACKed `failed` and kept as pending;
- reconciliation (`state=unacked`): younger than STALE_AFTER_S -> deliver; older -> ACK
  `dismissed`, keep it as pending for a manual copy, and tell the user once. Stale text is
  never written over the user's clipboard automatically.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Final

from app.companion.contract import RecentItem
from app.protocol.models import Ack, AckStatus, DeliveryStatus, ResultData, ResultKind, ResultRecord

Key = tuple[str, int]  # (daemon instance, result_seq)

RETRIES: Final = 3
RETRY_DELAY_S: Final = 1.0
STALE_AFTER_S: Final = 30.0
RECONCILE_INTERVAL_S: Final = 10.0
RECENT_LIMIT: Final = 10
PENDING_LIMIT: Final = 50
# The clipboard thread answers within ~2 s (5 attempts + Win+V re-assert); past this the
# verdict is considered lost and the attempt failed, so the queue never blocks forever.
IN_FLIGHT_TIMEOUT_S: Final = 15.0
_ACKED_MEMORY: Final = 2000


@dataclass(frozen=True)
class DeliveryJob:
    instance: str
    result_seq: int
    op_seq: int
    kind: ResultKind
    flow: str | None
    text: str
    final: bool
    spoken: bool = False
    needs_cue: bool = False
    manual: bool = False  # copy_result: one verified attempt, no retries, never blocks

    @property
    def key(self) -> Key:
        return (self.instance, self.result_seq)


@dataclass(frozen=True)
class StartDelivery:
    job: DeliveryJob


@dataclass(frozen=True)
class SendAck:
    instance: str
    acks: tuple[Ack, ...]


@dataclass(frozen=True)
class Delivered:
    job: DeliveryJob


@dataclass(frozen=True)
class DeliveryFailed:
    job: DeliveryJob


@dataclass(frozen=True)
class NotCopied:
    """Results that will not reach the clipboard automatically (now in the pending list)."""

    count: int


DeliveryEffect = StartDelivery | SendAck | Delivered | DeliveryFailed | NotCopied


@dataclass
class _Entry:
    job: DeliveryJob
    attempts: int = 0
    not_before: float = 0.0
    started: float = field(default=0.0)


def _record(
    record: ResultRecord,
    *,
    delivery: DeliveryStatus | None = None,
) -> ResultRecord:
    return ResultRecord(
        result_seq=record["result_seq"],
        op_seq=record["op_seq"],
        kind=record["kind"],
        flow=record["flow"],
        text=record["text"],
        final=record["final"],
        created_ts=record["created_ts"],
        age_s=record["age_s"],
        delivery=record["delivery"] if delivery is None else delivery,
    )


def _ack(result_seq: int, status: AckStatus) -> Ack:
    return Ack(result_seq=result_seq, status=status, verified=status == "delivered")


def job_from_record(instance: str, record: ResultRecord, *, manual: bool = False) -> DeliveryJob:
    return DeliveryJob(
        instance=instance,
        result_seq=record["result_seq"],
        op_seq=record["op_seq"],
        kind=record["kind"],
        flow=record["flow"],
        text=record["text"],
        final=record["final"],
        manual=manual,
    )


class DeliveryQueue:
    def __init__(self) -> None:
        self.instance = ""
        self._queue: list[_Entry] = []
        self._in_flight: _Entry | None = None
        self._acked: OrderedDict[Key, AckStatus] = OrderedDict()
        self._recent: list[RecentItem] = []  # newest first
        self._pending: OrderedDict[Key, RecentItem] = OrderedDict()  # oldest first

    # --- views ---------------------------------------------------------------------

    def recent(self) -> list[RecentItem]:
        return list(self._recent)

    def pending(self) -> list[RecentItem]:
        return list(self._pending.values())

    def queued_keys(self) -> list[Key]:
        keys = [entry.job.key for entry in self._queue]
        return ([self._in_flight.job.key] if self._in_flight else []) + keys

    def is_known(self, key: Key) -> bool:
        if key in self._acked or key in self._pending:
            return True
        if self._in_flight is not None and self._in_flight.job.key == key:
            return True
        return any(entry.job.key == key for entry in self._queue)

    def next_wakeup(self) -> float | None:
        """Monotonic time at which `tick` has something to do (None = nothing scheduled)."""
        if self._in_flight is not None:
            return self._in_flight.started + IN_FLIGHT_TIMEOUT_S
        if self._queue:
            return self._queue[0].not_before
        return None

    # --- inputs ---------------------------------------------------------------------

    def set_instance(self, instance: str, now: float) -> list[DeliveryEffect]:
        """A new daemon instance: queued results of the old one cannot be ACKed any more,
        so they move to the pending list (manual copy) instead of being written late."""
        if instance == self.instance:
            return []
        self.instance = instance
        moved = [entry for entry in self._queue if entry.job.instance != instance and not entry.job.manual]
        self._queue = [entry for entry in self._queue if entry not in moved]
        for entry in moved:
            self._move_to_pending(entry.job)
        effects: list[DeliveryEffect] = [NotCopied(len(moved))] if moved else []
        return effects + self._advance(now)

    def on_result(self, instance: str, data: ResultData, ts: float, now: float) -> list[DeliveryEffect]:
        """A `result` event. Every result goes to Recent; `needs_delivery` ones are queued.

        Reconciliation may have queued (or even delivered) it first: then Recent keeps its
        status, and a job still waiting takes the event's `needs_cue` and `spoken`."""
        key = (instance, data["result_seq"])
        if self.is_known(key):
            self._refresh_job(key, data)
            return []
        self._remember(
            instance,
            ResultRecord(
                result_seq=data["result_seq"],
                op_seq=data["op_seq"],
                kind=data["kind"],
                flow=data["flow"],
                text=data["text"],
                final=data["final"],
                created_ts=ts,
                age_s=0.0,
                delivery="pending" if data["needs_delivery"] else "daemon",
            ),
        )
        if not data["needs_delivery"]:
            return []
        job = DeliveryJob(
            instance=instance,
            result_seq=data["result_seq"],
            op_seq=data["op_seq"],
            kind=data["kind"],
            flow=data["flow"],
            text=data["text"],
            final=data["final"],
            spoken=data["spoken"],
            needs_cue=data["needs_cue"],
        )
        self._insert(_Entry(job))
        return self._advance(now)

    def on_unacked(self, instance: str, records: list[ResultRecord], now: float) -> list[DeliveryEffect]:
        """Reconciliation (`GET /results?state=unacked`, or a snapshot's `unacked`)."""
        if instance != self.instance:
            return []
        stale: list[ResultRecord] = []
        for record in records:
            if record.get("delivery") != "pending":
                continue
            key = (instance, record["result_seq"])
            if self.is_known(key):
                continue
            self._remember(instance, record)
            if record["age_s"] < STALE_AFTER_S:
                # Its event was missed (a poll gap): deliver it, but no cue (never a double cue).
                self._insert(_Entry(job_from_record(instance, record)))
            else:
                stale.append(record)
        effects: list[DeliveryEffect] = []
        if stale:
            for record in stale:
                self._set_status((instance, record["result_seq"]), "dismissed", record)
            effects.append(SendAck(instance, tuple(_ack(record["result_seq"], "dismissed") for record in stale)))
            effects.append(NotCopied(len(stale)))
        return effects + self._advance(now)

    def copy(self, instance: str, result_seq: int, now: float) -> list[DeliveryEffect]:
        """Manual copy of a Recent or Pending result (the user's click wins over the queue order)."""
        key = (instance, result_seq)
        item = self._pending.get(key) or next(
            (item for item in self._recent if (item.instance, item.record["result_seq"]) == key), None
        )
        if item is None:
            return []
        self._queue = [entry for entry in self._queue if entry.job.key != key]
        job = job_from_record(instance, item.record, manual=True)
        self._insert(_Entry(job))
        return self._advance(now)

    def on_delivery_done(self, key: Key, ok: bool, now: float) -> list[DeliveryEffect]:
        entry = self._in_flight
        if entry is None or entry.job.key != key:
            return []
        self._in_flight = None
        job = entry.job
        effects: list[DeliveryEffect] = []
        if ok:
            self._set_status(key, "delivered")
            effects.append(Delivered(job))
            # Last status wins (also over an earlier `dismissed`); an old instance would answer 409.
            if job.instance == self.instance:
                effects.append(SendAck(job.instance, (_ack(job.result_seq, "delivered"),)))
        elif job.manual:
            effects.append(DeliveryFailed(job))
        elif job.instance != self.instance:
            # The daemon restarted meanwhile: never retried (nor ACKed) on the new instance.
            self._move_to_pending(job)
            effects.append(NotCopied(1))
        else:
            entry.attempts += 1
            if entry.attempts <= RETRIES:
                entry.not_before = now + RETRY_DELAY_S
                self._queue.insert(0, entry)  # keeps blocking the later results
            else:
                self._set_status(key, "failed", self._find_record(key) or _record_from_job(job))
                effects.append(DeliveryFailed(job))
                if job.instance == self.instance:
                    effects.append(SendAck(job.instance, (_ack(job.result_seq, "failed"),)))
        return effects + self._advance(now)

    def tick(self, now: float) -> list[DeliveryEffect]:
        entry = self._in_flight
        if entry is not None and now - entry.started >= IN_FLIGHT_TIMEOUT_S:
            return self.on_delivery_done(entry.job.key, False, now)
        return self._advance(now)

    # --- internals ---------------------------------------------------------------------

    def _refresh_job(self, key: Key, data: ResultData) -> None:
        entries = ([self._in_flight] if self._in_flight is not None else []) + self._queue
        for entry in entries:
            if entry.job.key == key and not entry.job.manual:
                entry.job = replace(
                    entry.job,
                    needs_cue=entry.job.needs_cue or data["needs_cue"],
                    spoken=entry.job.spoken or data["spoken"],
                )

    def _insert(self, entry: _Entry) -> None:
        if entry.job.manual:
            # After other manual copies, before automatic deliveries.
            index = next((i for i, queued in enumerate(self._queue) if not queued.job.manual), len(self._queue))
            self._queue.insert(index, entry)
            return
        index = len(self._queue)
        for i, queued in enumerate(self._queue):
            if not queued.job.manual and queued.attempts == 0 and queued.job.result_seq > entry.job.result_seq:
                index = i
                break
        self._queue.insert(index, entry)

    def _advance(self, now: float) -> list[DeliveryEffect]:
        if self._in_flight is not None or not self._queue:
            return []
        head = self._queue[0]
        if head.not_before > now:
            return []
        self._queue.pop(0)
        head.started = now
        self._in_flight = head
        return [StartDelivery(head.job)]

    def _remember(self, instance: str, record: ResultRecord) -> None:
        key = (instance, record["result_seq"])
        self._recent = [item for item in self._recent if (item.instance, item.record["result_seq"]) != key]
        self._recent.insert(0, RecentItem(instance, _record(record)))
        del self._recent[RECENT_LIMIT:]

    def _find_record(self, key: Key) -> ResultRecord | None:
        for item in self._recent:
            if (item.instance, item.record["result_seq"]) == key:
                return item.record
        pending = self._pending.get(key)
        return pending.record if pending is not None else None

    def _add_pending(self, key: Key, record: ResultRecord) -> None:
        self._pending[key] = RecentItem(key[0], _record(record))
        while len(self._pending) > PENDING_LIMIT:
            self._pending.popitem(last=False)

    def _move_to_pending(self, job: DeliveryJob) -> None:
        """A queued job that will not be delivered automatically (its instance is gone)."""
        record = self._find_record(job.key) or _record_from_job(job)
        self._add_pending(job.key, record)

    def _set_status(self, key: Key, status: AckStatus, record: ResultRecord | None = None) -> None:
        self._acked[key] = status
        self._acked.move_to_end(key)
        while len(self._acked) > _ACKED_MEMORY:
            self._acked.popitem(last=False)
        self._recent = [
            RecentItem(item.instance, _record(item.record, delivery=status))
            if (item.instance, item.record["result_seq"]) == key
            else item
            for item in self._recent
        ]
        if status == "delivered":
            self._pending.pop(key, None)
            return
        known = record or self._find_record(key)
        if known is not None:
            self._add_pending(key, _record(known, delivery=status))


def _record_from_job(job: DeliveryJob, delivery: DeliveryStatus = "pending") -> ResultRecord:
    return ResultRecord(
        result_seq=job.result_seq,
        op_seq=job.op_seq,
        kind=job.kind,
        flow=job.flow,
        text=job.text,
        final=job.final,
        created_ts=0.0,
        age_s=0.0,
        delivery=delivery,
    )
