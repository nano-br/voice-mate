from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
from companion_core_fakes import FakeDaemon, wait_until

from app.companion.client import TOKEN_REREAD_S, DaemonClient, DaemonError, EventPoller
from app.protocol.models import EventsResponse, RegisterRequest, RegisterResponse


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _register(client: DaemonClient, key: str = "key-123456") -> RegisterResponse:
    return client.register(RegisterRequest(client_key=key, capabilities=["clipboard", "cues"], lease_s=40))


@pytest.fixture
def daemon() -> Iterator[FakeDaemon]:
    fake = FakeDaemon().start()
    yield fake
    fake.stop()


@pytest.fixture
def silent_server() -> Iterator[int]:
    """Accepts connections and never answers (a hung daemon)."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
                held.append(conn)
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield int(server.getsockname()[1])
    stop.set()
    thread.join(1)
    for conn in held:
        conn.close()
    server.close()


def test_probe_and_health(daemon: FakeDaemon) -> None:
    client = DaemonClient(daemon.port)
    assert client.probe()
    health = client.health()
    assert health["api_version"] == 2 and health["instance"] == daemon.instance


def test_offline_is_detected_fast() -> None:
    client = DaemonClient(_free_port())
    started = time.monotonic()
    assert not client.probe()
    with pytest.raises(DaemonError) as raised:
        client.trigger({"flow": "clipboard"})
    assert raised.value.kind == "offline"
    assert time.monotonic() - started < 2.5


def test_timeout_on_a_hung_daemon(silent_server: int) -> None:
    client = DaemonClient(silent_server)
    with pytest.raises(DaemonError) as raised:
        client.health(timeout=0.5)
    assert raised.value.kind == "timeout"


def test_register_and_long_poll(daemon: FakeDaemon) -> None:
    client = DaemonClient(daemon.port)
    registered = _register(client)
    assert registered["granted"] == ["clipboard", "cues"]
    assert registered["snapshot"]["state"]["state"] == "idle"
    threading.Timer(0.3, lambda: daemon.set_audio("down")).start()
    started = time.monotonic()
    response = client.events(registered["client_id"], registered["instance"], registered["cursor"], wait=5)
    assert time.monotonic() - started < 3
    assert [event["type"] for event in response["events"]] == ["health"]
    assert response["leases"] == ["clipboard", "cues"]
    empty = client.events(registered["client_id"], registered["instance"], response["cursor"], wait=1)
    assert empty["events"] == []


def test_unknown_client_is_410(daemon: FakeDaemon) -> None:
    client = DaemonClient(daemon.port)
    with pytest.raises(DaemonError) as raised:
        client.events("nobody", daemon.instance, 0, wait=0)
    assert raised.value.kind == "http" and raised.value.status == 410


def test_not_ready_is_503(daemon: FakeDaemon) -> None:
    daemon.ready = False
    client = DaemonClient(daemon.port)
    assert client.health()["ready"] is False
    with pytest.raises(DaemonError) as raised:
        _register(client)
    assert raised.value.status == 503


def test_token_is_read_only_when_auth_is_required(daemon: FakeDaemon) -> None:
    reads: list[int] = []

    def provider() -> str | None:
        reads.append(1)
        return "unused"

    client = DaemonClient(daemon.port, token_provider=provider)
    client.health()
    _register(client)
    assert reads == []


def test_bearer_token_is_reread_after_401_at_most_every_30_s() -> None:
    daemon = FakeDaemon(token="fresh-token").start()
    try:
        tokens = iter(["stale-token", "fresh-token"])
        reads: list[str] = []

        def provider() -> str | None:
            token = next(tokens)
            reads.append(token)
            return token

        client = DaemonClient(daemon.port, token_provider=provider)
        # No /health yet: the first 401 makes the client read the token and retry once.
        with pytest.raises(DaemonError) as raised:
            _register(client)
        assert raised.value.status == 401 and reads == ["stale-token"]
        # A persistent 401 does not run the (slow, wsl.exe) provider on every request.
        for _ in range(3):
            with pytest.raises(DaemonError):
                _register(client)
        assert reads == ["stale-token"]
        # 30 s later the token file is read again (it may have been recreated).
        assert client._token_read_at is not None
        client._token_read_at -= TOKEN_REREAD_S + 1
        assert _register(client)["client_id"]
        assert reads == ["stale-token", "fresh-token"]
        with pytest.raises(DaemonError) as raised:
            DaemonClient(daemon.port).results("unacked")
        assert raised.value.status == 401
    finally:
        daemon.stop()


def test_a_slow_token_read_does_not_block_requests_that_have_one(daemon: FakeDaemon) -> None:
    daemon.token = "t1"
    release = threading.Event()
    calls: list[int] = []

    def provider() -> str | None:
        calls.append(1)
        if len(calls) > 1:
            release.wait(5)  # a slow wsl.exe read
        return "t1"

    client = DaemonClient(daemon.port, token_provider=provider)
    client.health()
    assert client.token() == "t1"
    client._token_read_at = None
    assert client.invalidate_token()
    reader = threading.Thread(target=client.token)
    reader.start()
    assert wait_until(lambda: len(calls) == 2)
    started = time.monotonic()
    assert client.token() == "t1"  # the old token, at once, while the read is in flight
    assert _register(client)["client_id"]
    assert time.monotonic() - started < 2
    release.set()
    reader.join(5)


def test_poller_releases_a_registration_that_completes_after_stop(daemon: FakeDaemon) -> None:
    client = DaemonClient(daemon.port)
    poller: EventPoller | None = None

    def registration() -> RegisterRequest:
        assert poller is not None
        poller.stop()  # Quit while /register is in flight
        return RegisterRequest(client_key="poller-key-3", capabilities=["clipboard", "cues"])

    registered: list[RegisterResponse] = []
    poller = EventPoller(
        client,
        registration=registration,
        on_registered=registered.append,
        on_events=lambda response, changed: None,
        wait_s=1,
        retry_s=0.1,
    )
    poller.set_enabled(True)
    poller.start()
    assert wait_until(lambda: daemon.unregistered, timeout=5)
    assert registered == [] and poller.take_session() is None
    assert daemon.leases == {}


def test_poller_registers_polls_and_recovers(daemon: FakeDaemon) -> None:
    client = DaemonClient(daemon.port)
    registered: list[RegisterResponse] = []
    batches: list[tuple[EventsResponse, bool]] = []
    poller = EventPoller(
        client,
        registration=lambda: RegisterRequest(client_key="poller-key-1", capabilities=["clipboard", "cues"]),
        on_registered=registered.append,
        on_events=lambda response, changed: batches.append((response, changed)),
        wait_s=1,
        retry_s=0.1,
    )
    poller.start()
    try:
        assert not wait_until(lambda: registered, timeout=0.5)  # disabled: nothing happens
        poller.set_enabled(True)
        assert wait_until(lambda: len(registered) == 1)
        daemon.set_audio("down")
        assert wait_until(lambda: any(e["type"] == "health" for b, _c in batches for e in b["events"]))

        # The daemon forgot us (410): register again.
        daemon.forget_clients()
        assert wait_until(lambda: len(registered) == 2)

        # A lease expired while we stalled: take it over with the same key.
        daemon.expire_leases()
        daemon.set_audio("ok")
        assert wait_until(lambda: len(registered) == 3)
        assert {r["client_key"] for r in daemon.registrations} == {"poller-key-1"}

        # A restarted daemon does not know our client_id any more (410): register again.
        daemon.restart()
        assert wait_until(lambda: len(registered) == 4)
        session = poller.session()
        assert session is not None and session.instance == daemon.instance
    finally:
        poller.stop()


def test_a_stale_instance_gets_a_reset_with_a_snapshot(daemon: FakeDaemon) -> None:
    client = DaemonClient(daemon.port)
    registered = _register(client)
    response = client.events(registered["client_id"], "an-old-instance", 0, wait=0)
    assert response["reset"] is True
    assert response["events"][0]["type"] == "snapshot"
    assert response["instance"] == daemon.instance


def test_poller_registers_again_after_a_reset_for_a_new_instance(daemon: FakeDaemon) -> None:
    client = DaemonClient(daemon.port)
    registered: list[RegisterResponse] = []
    batches: list[tuple[EventsResponse, bool]] = []
    poller = EventPoller(
        client,
        registration=lambda: RegisterRequest(client_key="poller-key-2", capabilities=["cues"]),
        on_registered=registered.append,
        on_events=lambda response, changed: batches.append((response, changed)),
        wait_s=1,
        retry_s=0.1,
    )
    poller.set_enabled(True)
    poller.start()
    try:
        assert wait_until(lambda: len(registered) == 1)
        session = poller.session()
        assert session is not None
        session.instance = "an-old-instance"  # as if the daemon changed under the poll
        assert wait_until(lambda: any(changed for _b, changed in batches))
        assert wait_until(lambda: len(registered) == 2)
    finally:
        poller.stop()
