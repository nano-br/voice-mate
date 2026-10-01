"""Process lifecycle of the daemon: the readiness gate and shutdown requests.

The HTTP API is bound BEFORE the transcriber and handlers are built (the model
load takes 10 to 60 s), so Quit and Restart work while it loads. Until
`mark_ready`, a shutdown request (`POST /shutdown`, stdin EOF with
`--supervised`) exits the process at once: the model load cannot be interrupted
and there is nothing to clean up yet. After it, the request runs the graceful
stop that `main` registered (publish the `shutdown` event, stop the listener).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from typing import BinaryIO

from app.i18n import _
from app.protocol.models import ShutdownReason

StopCallback = Callable[[ShutdownReason], None]
ExitCallback = Callable[[int], None]


def _hard_exit(code: int) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass
    os._exit(code)


class Lifecycle:
    """Readiness flag plus a single, idempotent shutdown request."""

    def __init__(self, exit_now: ExitCallback = _hard_exit, exit_delay: float = 0.2) -> None:
        self._exit_now = exit_now
        # Lets the HTTP response to /shutdown reach the client before the process dies.
        self._exit_delay = exit_delay
        self._lock = threading.Lock()
        self._stop: StopCallback | None = None
        self._reason: ShutdownReason | None = None
        self._requested = threading.Event()

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._stop is not None

    @property
    def shutdown_reason(self) -> ShutdownReason | None:
        with self._lock:
            return self._reason

    def mark_ready(self, stop: StopCallback) -> bool:
        """Open the gate; later shutdown requests run `stop`. False = already shutting down."""
        with self._lock:
            if self._reason is not None:
                return False
            self._stop = stop
            return True

    def request_shutdown(self, reason: ShutdownReason) -> None:
        """Stop the daemon (once). Never blocks the caller (an HTTP request, the stdin watcher)."""
        with self._lock:
            if self._reason is not None:
                return
            self._reason = reason
            stop = self._stop
        self._requested.set()
        if stop is None:
            print(_("[VoiceMate] Shutdown requested while the engine loads: exiting now."), flush=True)
            threading.Thread(target=self._exit_soon, daemon=True, name="DaemonExit").start()
            return
        threading.Thread(target=stop, args=(reason,), daemon=True, name="DaemonShutdown").start()

    def wait_shutdown(self, timeout: float | None = None) -> bool:
        return self._requested.wait(timeout)

    def _exit_soon(self) -> None:
        time.sleep(self._exit_delay)
        self._exit_now(0)


def watch_stdin_eof(on_eof: Callable[[], None], stream: BinaryIO | None = None) -> threading.Thread | None:
    """`--supervised`: call `on_eof` once stdin reaches EOF (the supervisor closed it or died).

    Input is read and discarded. Returns None when the process has no stdin at all.
    Reads the raw file descriptor: a daemon thread blocked inside a buffered reader
    holds its lock, and the interpreter aborts at exit when it cannot take that lock.
    """
    source = stream
    if source is None:
        stdin = sys.stdin
        if stdin is None:
            return None
        source = stdin.buffer
    try:
        fd: int | None = source.fileno()
    except (OSError, ValueError):  # io.UnsupportedOperation (e.g. an in-memory stream)
        fd = None

    def _watch() -> None:
        try:
            while os.read(fd, 4096) if fd is not None else source.read(4096):
                pass
        except (OSError, ValueError):
            pass  # a broken or closed stdin means the supervisor is gone too
        print(_("[VoiceMate] stdin closed: shutting down."), flush=True)
        on_eof()

    thread = threading.Thread(target=_watch, daemon=True, name="StdinWatch")
    thread.start()
    return thread
