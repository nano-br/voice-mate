"""Process-level pieces of main: SIGTERM, the listener thread, the hard exit during the build."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable

import pytest

from app.daemon.lifecycle import Lifecycle
from app.main import _exit_now, _install_sigterm, _ListenerThread
from app.protocol.models import ShutdownReason


def test_sigterm_requests_a_supervisor_shutdown() -> None:
    previous = signal.getsignal(signal.SIGTERM)
    stopped: list[ShutdownReason] = []
    done = threading.Event()

    def _stop(reason: ShutdownReason) -> None:
        stopped.append(reason)
        done.set()

    lifecycle = Lifecycle(exit_now=lambda _code: None)
    lifecycle.mark_ready(_stop)
    try:
        _install_sigterm(lifecycle)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)  # what the interpreter does when the signal arrives
        assert done.wait(timeout=5.0)
        assert stopped == ["supervisor"]
    finally:
        signal.signal(signal.SIGTERM, previous)


class _NeverReturns:
    """Like the keyboard/mouse listeners: listen() blocks for good, stop() only unhooks."""

    def __init__(self) -> None:
        self.stopped = threading.Event()

    def listen(self) -> None:
        threading.Event().wait()

    def reinstall(self) -> None:
        return None

    def stop(self) -> None:
        self.stopped.set()


def test_the_main_thread_follows_the_lifecycle_even_if_listen_never_returns() -> None:
    listener = _NeverReturns()
    runner = _ListenerThread(listener)  # type: ignore[arg-type]
    runner.start()
    lifecycle = Lifecycle(exit_now=lambda _code: None)
    lifecycle.mark_ready(lambda _reason: listener.stop())
    threading.Timer(0.1, lambda: lifecycle.request_shutdown("user_quit")).start()
    # The loop main runs: it ends on the shutdown even though listen() is still blocked.
    while runner.is_alive() and not lifecycle.wait_shutdown(0.5):
        pass
    assert lifecycle.shutdown_reason == "user_quit"
    assert runner.is_alive()
    assert listener.stopped.wait(timeout=5.0)


def test_a_listener_failure_is_kept_for_the_main_thread() -> None:
    class Broken(_NeverReturns):
        def listen(self) -> None:
            raise RuntimeError("no readable keyboard")

    runner = _ListenerThread(Broken())  # type: ignore[arg-type]
    runner.start()
    runner.join(timeout=5.0)
    assert isinstance(runner.error, RuntimeError)


def test_the_hard_exit_kills_live_whisper_servers_first(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("app.main.kill_live_servers", lambda: calls.append("kill"))
    exit_fn: Callable[[int], None] = lambda code: calls.append(f"exit {code}")  # noqa: E731
    monkeypatch.setattr("app.main.hard_exit", exit_fn)
    _exit_now(0)
    assert calls == ["kill", "exit 0"]
