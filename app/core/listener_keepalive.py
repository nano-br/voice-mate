from __future__ import annotations

import sys
import threading

from app.core.input_listener import InputListener


class ListenerKeepalive:
    """Periodically reinstall the listener to recover from Windows
    silently removing the low-level hook.

    Why: WH_KEYBOARD_LL / WH_MOUSE_LL are removed by the OS without warning
    when the callback exceeds LowLevelHooksTimeout under heavy load.
    """

    def __init__(self, listener: InputListener, interval_seconds: float = 60.0) -> None:
        self._listener = listener
        self._interval = interval_seconds
        self._running = False
        self._timer: threading.Timer | None = None

    def start(self) -> None:
        self._running = True
        self._schedule_next()
        print(f"[Keepalive] Reinstalling listener every {self._interval:.0f}s")

    def stop(self) -> None:
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self) -> None:
        if not self._running:
            return
        self._timer = threading.Timer(self._interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self) -> None:
        if not self._running:
            return
        try:
            self._listener.reinstall()
        except Exception as exc:  # noqa: BLE001
            print(f"[Keepalive] Failed to reinstall listener: {exc}", file=sys.stderr)
        self._schedule_next()
