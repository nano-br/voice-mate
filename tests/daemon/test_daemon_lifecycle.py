"""Lifecycle: shutdown before ready exits at once; after ready it runs the graceful stop.
The --supervised stdin watcher turns EOF into a shutdown request."""

from __future__ import annotations

import io
import os
import threading

from app.daemon.lifecycle import Lifecycle, watch_stdin_eof
from app.protocol.models import ShutdownReason


class _Exit:
    def __init__(self) -> None:
        self.codes: list[int] = []
        self.called = threading.Event()

    def __call__(self, code: int) -> None:
        self.codes.append(code)
        self.called.set()


def test_shutdown_while_loading_exits_the_process_at_once() -> None:
    exit_now = _Exit()
    lifecycle = Lifecycle(exit_now=exit_now, exit_delay=0.0)
    assert not lifecycle.ready
    lifecycle.request_shutdown("user_quit")
    assert exit_now.called.wait(timeout=2.0)
    assert exit_now.codes == [0]
    assert lifecycle.shutdown_reason == "user_quit"
    # The build finishing afterwards must not start serving.
    assert lifecycle.mark_ready(lambda _reason: None) is False


def test_shutdown_after_ready_runs_the_graceful_stop_once() -> None:
    exit_now = _Exit()
    lifecycle = Lifecycle(exit_now=exit_now, exit_delay=0.0)
    stops: list[ShutdownReason] = []
    stopped = threading.Event()

    def _stop(reason: ShutdownReason) -> None:
        stops.append(reason)
        stopped.set()

    assert lifecycle.mark_ready(_stop)
    assert lifecycle.ready
    lifecycle.request_shutdown("restart")
    lifecycle.request_shutdown("supervisor")  # idempotent: the first reason wins
    assert stopped.wait(timeout=2.0)
    assert lifecycle.wait_shutdown(0)
    assert stops == ["restart"]
    assert exit_now.codes == []


def test_stdin_eof_triggers_the_callback() -> None:
    read_fd, write_fd = os.pipe()
    eof = threading.Event()
    with os.fdopen(read_fd, "rb") as reader:
        thread = watch_stdin_eof(eof.set, stream=reader)
        assert thread is not None
        with os.fdopen(write_fd, "wb") as writer:
            writer.write(b"ignored input\n")
            writer.flush()
            assert not eof.wait(timeout=0.2)  # input alone is not EOF
        assert eof.wait(timeout=5.0)  # the writer closed: EOF
        thread.join(timeout=5.0)


def test_stdin_already_at_eof() -> None:
    eof = threading.Event()
    watch_stdin_eof(eof.set, stream=io.BytesIO(b""))
    assert eof.wait(timeout=5.0)
