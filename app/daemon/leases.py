"""Client registry and the exclusive leases behind `POST /register`.

A lease (`clipboard`, `cues`) says "this client does that job; the daemon must
not". There is at most one holder per lease. Any request carrying the holder's
`client_id` renews it; a holder that stays silent for more than its `lease_s`
loses it and the daemon takes the job back (a client blocked in an `/events`
long poll counts as present). Registering with the `client_key` of an earlier
client (the same install, relaunched after a crash) takes over that client's
leases at once and retires the old `client_id`.

Expiry is lazy: it is evaluated whenever the registry is consulted, which is
exactly when the daemon needs to know who holds a lease.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, Literal, get_args

from app.protocol.models import Capability

LEASE_MIN_S: Final = 10
LEASE_MAX_S: Final = 120
LEASE_DEFAULT_S: Final = 40
# Bound on remembered clients (v1 consumers never unregister). The oldest client
# holding no lease and with no long poll in flight is forgotten first.
MAX_CLIENTS: Final = 256

CAPABILITIES: tuple[Capability, ...] = get_args(Capability)

ReleaseReason = Literal["expired", "unregistered", "taken_over"]


@dataclass(frozen=True)
class Release:
    """A lease a client lost (the daemon takes that job back, unless it was taken over)."""

    capability: Capability
    client_id: str
    client_name: str
    reason: ReleaseReason


# Called (outside the registry lock) for every lease a client loses.
ReleaseCallback = Callable[[Release], None]


def clamp_lease(value: int | None) -> int:
    """`lease_s` as granted: default 40, clamped to 10..120."""
    if value is None:
        return LEASE_DEFAULT_S
    return max(LEASE_MIN_S, min(LEASE_MAX_S, value))


@dataclass
class _Client:
    client_id: str
    client_key: str
    name: str
    lease_s: int
    last_seen: float
    polls: int = 0  # /events long polls in flight


@dataclass(frozen=True)
class Registration:
    client_id: str
    granted: list[Capability]
    lease_s: int
    replaced: list[str]  # client ids retired by a client_key takeover


class LeaseRegistry:
    """Thread-safe registry of clients and lease holders."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] = lambda: secrets.token_hex(8),
        on_release: ReleaseCallback | None = None,
        max_clients: int = MAX_CLIENTS,
    ) -> None:
        self._clock = clock
        self._new_id = id_factory
        self._on_release = on_release
        self._max_clients = max_clients
        self._lock = threading.Lock()
        self._clients: dict[str, _Client] = {}
        self._holders: dict[Capability, str] = {}

    # -- registration ------------------------------------------------------
    def register(
        self,
        *,
        client_key: str = "",
        name: str = "",
        capabilities: Iterable[Capability] = (),
        lease_s: int | None = None,
    ) -> Registration:
        wanted = set(capabilities)
        requested = [cap for cap in CAPABILITIES if cap in wanted]
        released: list[Release] = []
        with self._lock:
            now = self._clock()
            released += self._expire_locked(now)
            client_id = self._unique_id_locked()
            replaced = {
                c.client_id: c.name for c in self._clients.values() if client_key and c.client_key == client_key
            }
            for old_id in replaced:
                del self._clients[old_id]
            granted: list[Capability] = []
            for cap in CAPABILITIES:
                holder = self._holders.get(cap)
                if holder is not None and holder in replaced:
                    del self._holders[cap]  # the same install, relaunched: its leases move on
                    if cap not in requested:
                        released.append(Release(cap, holder, replaced[holder], "taken_over"))
                    holder = None
                if cap in requested and holder is None:
                    self._holders[cap] = client_id
                    granted.append(cap)
            granted_lease = clamp_lease(lease_s)
            self._clients[client_id] = _Client(client_id, client_key, name, granted_lease, now)
            self._evict_locked()
        self._notify(released)
        return Registration(client_id=client_id, granted=granted, lease_s=granted_lease, replaced=list(replaced))

    def unregister(self, client_id: str) -> bool:
        """Forget the client and release its leases at once. False = unknown client."""
        with self._lock:
            client = self._clients.pop(client_id, None)
            if client is None:
                return False
            released = self._drop_holder_locked(client, "unregistered")
        self._notify(released)
        return True

    # -- presence ----------------------------------------------------------
    def is_known(self, client_id: str) -> bool:
        with self._lock:
            return client_id in self._clients

    def touch(self, client_id: str) -> bool:
        """Renew the client's leases (if they have not expired yet). False = unknown client."""
        with self._lock:
            now = self._clock()
            released = self._expire_locked(now)
            client = self._clients.get(client_id)
            if client is not None:
                client.last_seen = now
        self._notify(released)
        return client is not None

    def begin_poll(self, client_id: str) -> bool:
        """A long poll starts: the client counts as present until `end_poll`. False = unknown."""
        with self._lock:
            now = self._clock()
            released = self._expire_locked(now)
            client = self._clients.get(client_id)
            if client is not None:
                client.last_seen = now
                client.polls += 1
        self._notify(released)
        return client is not None

    def end_poll(self, client_id: str) -> None:
        with self._lock:
            client = self._clients.get(client_id)
            if client is not None:
                client.polls = max(0, client.polls - 1)
                client.last_seen = self._clock()

    # -- queries -----------------------------------------------------------
    def holder(self, capability: Capability) -> str | None:
        return self.holders().get(capability)

    def holders(self) -> dict[Capability, str]:
        """Current (unexpired) holders, evaluated once: use it for decisions that must agree."""
        with self._lock:
            released = self._expire_locked(self._clock())
            holders = dict(self._holders)
        self._notify(released)
        return holders

    def leases_of(self, client_id: str) -> list[Capability]:
        holders = self.holders()
        return [cap for cap in CAPABILITIES if holders.get(cap) == client_id]

    def polling(self, client_id: str) -> bool:
        """The client has an /events long poll in flight."""
        with self._lock:
            client = self._clients.get(client_id)
            return client is not None and client.polls > 0

    def name_of(self, client_id: str) -> str:
        with self._lock:
            client = self._clients.get(client_id)
            return client.name if client is not None else ""

    # -- internals ---------------------------------------------------------
    def _unique_id_locked(self) -> str:
        while True:
            client_id = self._new_id()
            if client_id not in self._clients:
                return client_id

    def _expire_locked(self, now: float) -> list[Release]:
        released: list[Release] = []
        for cap, client_id in list(self._holders.items()):
            client = self._clients.get(client_id)
            if client is None or (client.polls == 0 and now - client.last_seen > client.lease_s):
                del self._holders[cap]
                released.append(Release(cap, client_id, client.name if client is not None else "", "expired"))
        return released

    def _drop_holder_locked(self, client: _Client, reason: ReleaseReason) -> list[Release]:
        released: list[Release] = []
        for cap, holder in list(self._holders.items()):
            if holder == client.client_id:
                del self._holders[cap]
                released.append(Release(cap, client.client_id, client.name, reason))
        return released

    def _evict_locked(self) -> None:
        if len(self._clients) <= self._max_clients:
            return
        holding = set(self._holders.values())
        idle = sorted(
            (c for c in self._clients.values() if c.client_id not in holding and c.polls == 0),
            key=lambda c: c.last_seen,
        )
        for client in idle[: len(self._clients) - self._max_clients]:
            del self._clients[client.client_id]

    def _notify(self, released: list[Release]) -> None:
        if self._on_release is None:
            return
        for release in released:
            self._on_release(release)
