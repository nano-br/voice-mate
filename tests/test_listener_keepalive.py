from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.services.listener_keepalive import ListenerKeepalive


class FakeListener:
    """Stub estrutural compatível com InputListener Protocol."""

    def __init__(self) -> None:
        self.reinstall_calls = 0
        self.event = threading.Event()

    def listen(self, on_toggle: Callable[[], None]) -> None:
        pass

    def reinstall(self) -> None:
        self.reinstall_calls += 1
        self.event.set()

    def stop(self) -> None:
        pass


class FlakyListener(FakeListener):
    def reinstall(self) -> None:
        self.reinstall_calls += 1
        self.event.set()
        if self.reinstall_calls == 1:
            raise RuntimeError("boom")


def test_keepalive_calls_reinstall_periodically() -> None:
    listener = FakeListener()
    keepalive = ListenerKeepalive(listener, interval_seconds=0.05)
    keepalive.start()
    try:
        assert listener.event.wait(timeout=1.0), "reinstall não foi chamado"
    finally:
        keepalive.stop()
    assert listener.reinstall_calls >= 1


def test_keepalive_stop_prevents_further_calls() -> None:
    listener = FakeListener()
    keepalive = ListenerKeepalive(listener, interval_seconds=0.05)
    keepalive.start()
    listener.event.wait(timeout=1.0)
    keepalive.stop()
    snapshot = listener.reinstall_calls
    time.sleep(0.25)
    # tolerância: uma chamada podia estar em vôo quando stop foi chamado
    assert listener.reinstall_calls <= snapshot + 1


def test_keepalive_survives_reinstall_exception() -> None:
    listener = FlakyListener()
    keepalive = ListenerKeepalive(listener, interval_seconds=0.03)
    keepalive.start()
    try:
        deadline = time.monotonic() + 1.5
        while listener.reinstall_calls < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        keepalive.stop()
    assert listener.reinstall_calls >= 2, "exceção matou o ciclo"


def test_keepalive_stop_is_safe_before_start() -> None:
    listener = FakeListener()
    keepalive = ListenerKeepalive(listener, interval_seconds=1.0)
    keepalive.stop()  # não deve levantar
    assert listener.reinstall_calls == 0
