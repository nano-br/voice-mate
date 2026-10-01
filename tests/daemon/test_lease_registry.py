"""LeaseRegistry: exclusive clipboard/cues leases, client_key takeover, expiry and renewal."""

from __future__ import annotations

import itertools

import pytest

from app.daemon.leases import LEASE_DEFAULT_S, LeaseRegistry, clamp_lease
from app.protocol.models import Capability


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _registry(clock: Clock, released: list[tuple[str, str, str]] | None = None) -> LeaseRegistry:
    ids = (f"c{n}" for n in itertools.count(1))
    sink = released if released is not None else []
    return LeaseRegistry(
        clock=clock,
        id_factory=lambda: next(ids),
        on_release=lambda release: sink.append((release.capability, release.client_id, release.reason)),
    )


@pytest.mark.parametrize(("requested", "granted"), [(None, 40), (1, 10), (10, 10), (55, 55), (120, 120), (999, 120)])
def test_lease_is_clamped(requested: int | None, granted: int) -> None:
    assert clamp_lease(requested) == granted


def test_first_client_gets_the_leases_and_a_second_does_not() -> None:
    reg = _registry(Clock())
    first = reg.register(name="companion", capabilities=["clipboard", "cues"])
    second = reg.register(name="other", capabilities=["clipboard", "cues"])
    assert first.granted == ["clipboard", "cues"]
    assert first.lease_s == LEASE_DEFAULT_S
    assert second.granted == []
    assert reg.holders() == {"clipboard": first.client_id, "cues": first.client_id}
    assert reg.leases_of(second.client_id) == []


def test_unknown_capabilities_are_ignored_and_v1_register_gets_none() -> None:
    reg = _registry(Clock())
    plain = reg.register()
    assert plain.granted == []
    assert reg.is_known(plain.client_id)
    caps: list[Capability] = ["cues"]
    assert reg.register(capabilities=caps).granted == ["cues"]


def test_same_client_key_takes_over_the_leases_at_once() -> None:
    """Relaunch after a crash: the new process registers with the persisted key and gets
    its leases back immediately, instead of waiting for them to expire."""
    released: list[tuple[str, str, str]] = []
    reg = _registry(Clock(), released)
    old = reg.register(client_key="install-1", capabilities=["clipboard", "cues"])
    new = reg.register(client_key="install-1", capabilities=["clipboard", "cues"])
    assert new.granted == ["clipboard", "cues"]
    assert new.replaced == [old.client_id]
    assert not reg.is_known(old.client_id)  # the old id answers 410 from now on
    assert reg.leases_of(new.client_id) == ["clipboard", "cues"]
    assert released == []  # moved, not released: the daemon never took the job back


def test_takeover_releases_leases_the_new_client_no_longer_wants() -> None:
    released: list[tuple[str, str, str]] = []
    reg = _registry(Clock(), released)
    old = reg.register(client_key="k", capabilities=["clipboard", "cues"])
    new = reg.register(client_key="k", capabilities=["cues"])
    assert new.granted == ["cues"]
    assert reg.holder("clipboard") is None
    assert released == [("clipboard", old.client_id, "taken_over")]


def test_another_key_cannot_take_over() -> None:
    reg = _registry(Clock())
    reg.register(client_key="a", capabilities=["cues"])
    assert reg.register(client_key="b", capabilities=["cues"]).granted == []


def test_lease_expires_after_lease_s_of_silence_and_touch_renews_it() -> None:
    clock = Clock()
    released: list[tuple[str, str, str]] = []
    reg = _registry(clock, released)
    client = reg.register(capabilities=["cues"], lease_s=10)

    clock.now += 9
    assert reg.touch(client.client_id)  # renewed at t+9
    clock.now += 9
    assert reg.holder("cues") == client.client_id  # 9 s since the renewal
    clock.now += 2
    assert reg.holder("cues") is None  # 11 s of silence: expired
    assert released == [("cues", client.client_id, "expired")]
    # The client stays known (its poll still works) but must register again for the lease.
    assert reg.is_known(client.client_id)
    assert reg.leases_of(client.client_id) == []
    assert reg.touch(client.client_id)
    assert reg.holder("cues") is None  # an expired lease is not revived by a renewal


def test_a_client_blocked_in_a_long_poll_keeps_its_lease() -> None:
    clock = Clock()
    reg = _registry(clock)
    client = reg.register(capabilities=["clipboard"], lease_s=10)
    assert reg.begin_poll(client.client_id)
    clock.now += 30  # the poll lasts longer than the lease
    assert reg.holder("clipboard") == client.client_id
    reg.end_poll(client.client_id)
    clock.now += 5
    assert reg.holder("clipboard") == client.client_id  # end_poll counts as activity


def test_unregister_releases_at_once_and_unknown_ids_are_reported() -> None:
    released: list[tuple[str, str, str]] = []
    reg = _registry(Clock(), released)
    client = reg.register(capabilities=["clipboard", "cues"])
    assert reg.unregister(client.client_id)
    assert reg.holders() == {}
    assert sorted(released) == [
        ("clipboard", client.client_id, "unregistered"),
        ("cues", client.client_id, "unregistered"),
    ]
    assert not reg.unregister(client.client_id)
    assert not reg.touch("nope")
    assert not reg.begin_poll("nope")


def test_releases_carry_the_client_name_even_after_it_is_forgotten() -> None:
    names: list[str] = []
    reg = LeaseRegistry(clock=Clock(), on_release=lambda release: names.append(release.client_name))
    client = reg.register(name="VoiceMate companion", capabilities=["cues"])
    reg.unregister(client.client_id)
    assert names == ["VoiceMate companion"]


def test_the_lease_is_free_again_for_another_client_after_expiry() -> None:
    clock = Clock()
    reg = _registry(clock)
    reg.register(capabilities=["cues"], lease_s=10)
    clock.now += 11
    late = reg.register(capabilities=["cues"])
    assert late.granted == ["cues"]


def test_registry_forgets_the_oldest_idle_clients_beyond_its_bound() -> None:
    clock = Clock()
    ids = (f"c{n}" for n in itertools.count(1))
    reg = LeaseRegistry(clock=clock, id_factory=lambda: next(ids), max_clients=3)
    holder = reg.register(capabilities=["cues"])
    for _n in range(4):
        clock.now += 1
        reg.register()
    assert reg.is_known(holder.client_id)  # a lease holder is never evicted
    assert not reg.is_known("c2")
    assert reg.is_known("c5")
