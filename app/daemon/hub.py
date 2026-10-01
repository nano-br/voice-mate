"""Engine-side state of the daemon API v2: one place that publishes and serves it.

The engine (recording session, handlers, audio probe) reports what happens; the
hub keeps the current operation (`StateData`), appends typed events to the
journal, tracks deliveries and consults the leases. The HTTP server only
translates requests into hub calls.

Lease decisions are made ONCE per publication, under the hub lock: the same
holder snapshot decides the per-client flags (`needs_cue`, `needs_delivery`)
and what the daemon does itself (`Publication.play_cue`, `write_clipboard`).
So a lease that changes hands mid-flight can never produce a double cue, nor a
result that nobody writes to the clipboard.

A cancelled operation (`POST /cancel`) publishes nothing more: results,
errors and warnings for its `op_seq` are dropped here. After the `shutdown`
event nothing is appended any more (clients consider this instance gone), and
the daemon does every job itself again.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Final, cast

from app.daemon.delivery import DeliveryTracker
from app.daemon.journal import DEFAULT_CAPACITY, EventJournal, JournalEntry
from app.daemon.leases import LeaseRegistry, Release
from app.i18n import _
from app.protocol.models import (
    API_VERSION,
    AckResponse,
    AckStatus,
    AudioHealth,
    Capability,
    ErrorCode,
    ErrorData,
    Event,
    EventsResponse,
    EventType,
    FlowKind,
    Phase,
    RegisterResponse,
    ResultData,
    ResultKind,
    ResultsQuery,
    ResultsResponse,
    SessionState,
    ShutdownReason,
    SnapshotData,
    SnapshotEvent,
    StateData,
    WarningCode,
    WarningData,
)

# Operations remembered as cancelled (their late results are dropped). Only the
# last few can still be producing anything.
_CANCELLED_MEMORY: Final = 64
# Event types whose data carries a per-client `needs_cue`.
_CUED_TYPES: Final[frozenset[EventType]] = frozenset({"state", "result", "error", "warning"})


class UnknownClient(Exception):
    """The `client_id` is not registered with this daemon instance (HTTP 410)."""


class InstanceMismatch(Exception):
    """The request names another daemon instance (HTTP 409)."""


@dataclass(frozen=True)
class Publication:
    """Outcome of publishing a result/error/warning, for the code that published it."""

    published: bool  # False: the operation was cancelled, nothing was published
    write_clipboard: bool  # nobody holds the clipboard lease: the daemon writes it itself
    play_cue: bool  # nobody holds the cues lease: the daemon plays its own beep


# Without a hub (tests, a handler used on its own) the daemon does every job itself.
DAEMON_ONLY: Final = Publication(published=True, write_clipboard=True, play_cue=True)
DROPPED: Final = Publication(published=False, write_clipboard=False, play_cue=False)


@dataclass(frozen=True)
class _Operation:
    op_seq: int = 0
    state: SessionState = "idle"
    phase: Phase | None = None
    flow: str | None = None
    flow_kind: FlowKind | None = None
    client_id: str | None = None
    mic_live: bool = False


def _state_data(op: _Operation) -> StateData:
    return {
        "state": op.state,
        "phase": op.phase,
        "op_seq": op.op_seq,
        "flow": op.flow,
        "flow_kind": op.flow_kind,
        "client_id": op.client_id,
        "mic_live": op.mic_live,
        "needs_cue": False,  # per client, set when rendered
    }


class EventHub:
    """Thread-safe API v2 state. Lock order: hub lock, then the components' own locks."""

    def __init__(
        self,
        *,
        instance: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        journal_capacity: int = DEFAULT_CAPACITY,
        registry: LeaseRegistry | None = None,
        deliveries: DeliveryTracker | None = None,
    ) -> None:
        # Random per daemon process: seqs restart at 0 with every daemon, so a client
        # that sees a new instance must start over (and register again).
        self.instance = instance or secrets.token_hex(4)
        self._clock = clock
        self._wall = wall
        self._started = clock()
        self._lock = threading.Lock()
        self._journal = EventJournal(journal_capacity, wall=wall)
        self._leases = registry if registry is not None else LeaseRegistry(clock=clock, on_release=self._on_release)
        self._deliveries = deliveries if deliveries is not None else DeliveryTracker(clock=clock, wall=wall)
        self._op = _Operation()
        self._audio: AudioHealth = "unknown"
        self._result_seq = 0
        self._cancelled: deque[int] = deque(maxlen=_CANCELLED_MEMORY)
        self._shut_down = False

    # -- read-only views -----------------------------------------------------
    @property
    def uptime_s(self) -> int:
        return int(self._clock() - self._started)

    @property
    def audio(self) -> AudioHealth:
        with self._lock:
            return self._audio

    @property
    def cursor(self) -> int:
        return self._journal.cursor

    def state(self) -> StateData:
        with self._lock:
            return _state_data(self._op)

    def snapshot(self) -> SnapshotData:
        with self._lock:
            return self._snapshot_locked()

    # -- producer side (the engine) -----------------------------------------
    def set_state(
        self,
        op_seq: int,
        state: SessionState,
        *,
        flow: str | None,
        flow_kind: FlowKind | None,
        client_id: str | None,
        phase: Phase | None = None,
    ) -> None:
        """Enter `state` for `op_seq` (mic_live starts false). Idle drops flow and phase."""
        if state == "idle":
            new = _Operation(op_seq=op_seq, state="idle", client_id=client_id)
        else:
            new = _Operation(
                op_seq=op_seq,
                state=state,
                phase=phase if state == "processing" else None,
                flow=flow,
                flow_kind=flow_kind,
                client_id=client_id,
            )
        with self._lock:
            self._apply_locked(new)

    def set_phase(self, op_seq: int, phase: Phase) -> None:
        """Change the phase of `op_seq` while it is processing (ignored for any other op)."""
        with self._lock:
            current = self._op
            if current.op_seq == op_seq and current.state == "processing":
                self._apply_locked(replace(current, phase=phase))

    def set_mic_live(self, op_seq: int) -> bool:
        """The capture stream of `op_seq` is open. True = the daemon plays the start beep itself."""
        with self._lock:
            current = self._op
            if current.op_seq != op_seq or current.state != "recording" or current.mic_live:
                return False
            entry = self._apply_locked(replace(current, mic_live=True))
            return entry is not None and entry.cue_holder is None

    def set_idle(self, op_seq: int) -> None:
        """Return to idle if `op_seq` is still the current operation (race-free)."""
        with self._lock:
            current = self._op
            if current.op_seq == op_seq and current.state != "idle":
                self._apply_locked(_Operation(op_seq=op_seq, state="idle", client_id=current.client_id))

    def publish_result(
        self,
        text: str,
        *,
        op_seq: int,
        kind: ResultKind,
        flow: str | None,
        final: bool,
        spoken: bool = False,
    ) -> Publication:
        with self._lock:
            if op_seq in self._cancelled:
                return DROPPED
            if self._shut_down:
                return DAEMON_ONLY
            holders = self._leases.holders()
            clipboard_holder = holders.get("clipboard")
            cue_holder = holders.get("cues")
            self._result_seq += 1
            data: ResultData = {
                "result_seq": self._result_seq,
                "op_seq": op_seq,
                "kind": kind,
                "flow": flow,
                "text": text,
                "needs_delivery": False,  # per client, set when rendered
                "final": final,
                "spoken": spoken,
                "needs_cue": False,
            }
            self._deliveries.add(
                result_seq=self._result_seq,
                op_seq=op_seq,
                kind=kind,
                flow=flow,
                text=text,
                final=final,
                delivery="pending" if clipboard_holder is not None else "daemon",
            )
            self._journal.append("result", data, cue_holder=cue_holder, clipboard_holder=clipboard_holder)
        return Publication(published=True, write_clipboard=clipboard_holder is None, play_cue=cue_holder is None)

    def publish_error(self, code: ErrorCode, *, detail: str, message: str, op_seq: int) -> Publication:
        data: ErrorData = {"code": code, "detail": detail, "message": message, "op_seq": op_seq, "needs_cue": False}
        return self._publish_notice("error", data, op_seq)

    def publish_warning(self, code: WarningCode, *, detail: str, message: str, op_seq: int) -> Publication:
        data: WarningData = {"code": code, "detail": detail, "message": message, "op_seq": op_seq, "needs_cue": False}
        return self._publish_notice("warning", data, op_seq)

    def publish_health(self, audio: AudioHealth) -> None:
        """Audio server state; an event only when it changes."""
        with self._lock:
            if audio == self._audio:
                return
            self._audio = audio
            if not self._shut_down:
                self._journal.append("health", {"audio": audio})

    def publish_shutdown(self, reason: ShutdownReason) -> bool:
        """Last event of this instance; wakes every pending long poll. False = already published."""
        with self._lock:
            if self._shut_down:
                return False
            self._shut_down = True
            self._journal.append("shutdown", {"reason": reason})
        self._journal.close()
        return True

    def cancel(self, op_seq: int) -> None:
        """Nothing more is published for `op_seq` (results, errors and warnings are dropped)."""
        with self._lock:
            if op_seq not in self._cancelled:
                self._cancelled.append(op_seq)

    def is_cancelled(self, op_seq: int) -> bool:
        with self._lock:
            return op_seq in self._cancelled

    # -- consumer side (the HTTP API) ----------------------------------------
    def register_plain(self) -> str:
        """v1 registration: a `client_id` with no lease (the Windows script, auto-registration)."""
        return self._leases.register().client_id

    def register(
        self,
        *,
        client_key: str = "",
        name: str = "",
        capabilities: Iterable[Capability] = (),
        lease_s: int | None = None,
    ) -> RegisterResponse:
        # Grant and snapshot under the hub lock: no event can slip between them, so the
        # client sees every live transition that happens while it holds the leases.
        with self._lock:
            registration = self._leases.register(
                client_key=client_key, name=name, capabilities=capabilities, lease_s=lease_s
            )
            snapshot = self._snapshot_locked()
            cursor = self._journal.cursor
        if name or registration.granted:
            print(
                _("[VoiceMate] Client {client} registered (leases: {leases}).").format(
                    client=name or registration.client_id, leases=", ".join(registration.granted) or "-"
                )
            )
        return {
            "client_id": registration.client_id,
            "instance": self.instance,
            "api_version": API_VERSION,
            "granted": registration.granted,
            "lease_s": registration.lease_s,
            "cursor": cursor,
            "snapshot": snapshot,
        }

    def unregister(self, client_id: str) -> None:
        if not self._leases.unregister(client_id):
            raise UnknownClient(client_id)

    def is_known(self, client_id: str) -> bool:
        return self._leases.is_known(client_id)

    def touch(self, client_id: str) -> bool:
        """Any request carrying a `client_id` renews that client's leases."""
        return self._leases.touch(client_id)

    def leases_of(self, client_id: str) -> list[Capability]:
        return self._leases.leases_of(client_id)

    def polling(self, client_id: str) -> bool:
        """The client has an /events long poll in flight."""
        return self._leases.polling(client_id)

    def client_name(self, client_id: str) -> str:
        return self._leases.name_of(client_id)

    def holder(self, capability: Capability) -> str | None:
        return self._leases.holder(capability)

    def poll(self, client_id: str, *, instance: str | None, since: int | None, wait: float) -> EventsResponse:
        """`GET /events`: events after `since`, waiting up to `wait` s; a reset+snapshot when the
        instance differs or the cursor is unknown/stale."""
        if not self._leases.begin_poll(client_id):
            raise UnknownClient(client_id)
        try:
            events: list[Event] | None = None
            cursor = 0
            if instance == self.instance and since is not None:
                read = self._journal.read(since, wait)
                if not read.stale:
                    events = [self._render(entry, client_id) for entry in read.entries]
                    cursor = read.cursor
        finally:
            self._leases.end_poll(client_id)
        reset = events is None
        if events is None:
            events = self._reset_events()
            cursor = events[0]["seq"]  # the snapshot is the state at this cursor
        return {
            "instance": self.instance,
            "cursor": cursor,
            "reset": reset,
            "events": events,
            "leases": self._leases.leases_of(client_id),
        }

    def ack(self, client_id: str, *, instance: str, acks: Iterable[tuple[int, AckStatus]]) -> AckResponse:
        if not self._leases.touch(client_id):
            raise UnknownClient(client_id)
        if instance != self.instance:
            raise InstanceMismatch(instance)
        return {"ok": True, "unacked": self._deliveries.ack(acks)}

    def results(self, which: ResultsQuery, limit: int) -> ResultsResponse:
        return {"instance": self.instance, "results": self._deliveries.query(which, limit)}

    # -- internals -------------------------------------------------------------
    def _apply_locked(self, new: _Operation) -> JournalEntry | None:
        if new == self._op:
            return None
        self._op = new
        if self._shut_down:
            return None
        cue_holder = self._leases.holders().get("cues")
        return self._journal.append("state", _state_data(new), cue_holder=cue_holder)

    def _publish_notice(self, event_type: EventType, data: ErrorData | WarningData, op_seq: int) -> Publication:
        with self._lock:
            if op_seq in self._cancelled:
                return DROPPED
            if self._shut_down:
                return DAEMON_ONLY
            cue_holder = self._leases.holders().get("cues")
            self._journal.append(event_type, data, cue_holder=cue_holder)
        return Publication(published=True, write_clipboard=False, play_cue=cue_holder is None)

    def _snapshot_locked(self) -> SnapshotData:
        return {"state": _state_data(self._op), "unacked": self._deliveries.pending(), "audio": self._audio}

    def _reset_events(self) -> list[Event]:
        with self._lock:
            snapshot = self._snapshot_locked()
            cursor = self._journal.cursor
        event: SnapshotEvent = {"seq": cursor, "ts": self._wall(), "type": "snapshot", "data": snapshot}
        return [event]

    @staticmethod
    def _render(entry: JournalEntry, client_id: str) -> Event:
        data = dict(entry.data)
        if entry.type in _CUED_TYPES:
            data["needs_cue"] = entry.cue_holder is not None and entry.cue_holder == client_id
        if entry.type == "result":
            data["needs_delivery"] = entry.clipboard_holder is not None and entry.clipboard_holder == client_id
        return cast(Event, {"seq": entry.seq, "ts": entry.ts, "type": entry.type, "data": data})

    @staticmethod
    def _on_release(release: Release) -> None:
        # Called by the registry outside its lock (possibly inside the hub lock): print only.
        client = release.client_name or release.client_id
        if release.reason == "expired":
            print(
                _("[VoiceMate] ⚠ Lease '{lease}' of client {client} expired: the daemon takes that job back.").format(
                    lease=release.capability, client=client
                )
            )
        elif release.reason == "unregistered":
            print(
                _("[VoiceMate] Lease '{lease}' released by client {client}: the daemon takes that job back.").format(
                    lease=release.capability, client=client
                )
            )
