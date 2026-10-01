"""Thread hop between the controller and the Qt GUI thread.

The controller calls its listeners from its dispatcher thread, and `quit`'s
`on_done` from another controller thread; widgets may only be touched from the
GUI thread. Every callback here only emits a signal connected with
`QueuedConnection` to a slot of this object (which lives in the GUI thread), so
the work always runs later on the GUI thread, in emission order, also when the
callback happens to run on the GUI thread itself (no re-entrancy).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QObject, Qt, Signal, Slot

from app.companion.contract import CompanionController, CompanionSnapshot, Notification, Unsubscribe

log = logging.getLogger(__name__)

T = TypeVar("T")


class ControllerBridge(QObject):
    """Delivers snapshots and notifications on the GUI thread; runs slow calls off it."""

    snapshot_changed = Signal(object)  # CompanionSnapshot, newest only (older revs are dropped)
    notification_received = Signal(object)  # Notification

    _snapshot_posted = Signal(object)
    _notification_posted = Signal(object)
    _call_posted = Signal(object)

    def __init__(self, controller: CompanionController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._latest: CompanionSnapshot = controller.snapshot()
        self._unsubscribe: list[Unsubscribe] = []
        queued = Qt.ConnectionType.QueuedConnection
        self._snapshot_posted.connect(self._on_snapshot, queued)
        self._notification_posted.connect(self._on_notification, queued)
        self._call_posted.connect(self._on_call, queued)

    @property
    def snapshot(self) -> CompanionSnapshot:
        """The newest snapshot delivered to the GUI thread."""
        return self._latest

    def attach(self) -> None:
        """Subscribe to the controller. Connect to the public signals first: `subscribe`
        delivers the current snapshot at once (it still reaches the slots queued)."""
        if self._unsubscribe:
            return
        self._unsubscribe = [
            self.controller.subscribe(self._snapshot_posted.emit),
            self.controller.subscribe_notifications(self._notification_posted.emit),
        ]

    def detach(self) -> None:
        for unsubscribe in self._unsubscribe:
            try:
                unsubscribe()
            except Exception:
                log.warning("unsubscribe failed", exc_info=True)
        self._unsubscribe = []

    def call_soon(self, callback: Callable[[], None]) -> None:
        """Run `callback` on the GUI thread (safe to call from any thread)."""
        self._call_posted.emit(callback)

    def run_async(
        self,
        work: Callable[[], T],
        on_done: Callable[[T], None],
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        """Run `work` on a short-lived thread; `on_done(result)` or `on_error(exc)` on the GUI thread.

        For controller calls that may wait on another thread (apply_settings re-registers
        hotkeys on the Win32 thread, check_hotkey tries a registration): the GUI never blocks.
        """

        def runner() -> None:
            try:
                result = work()
            except BaseException as exc:
                log.warning("background call failed", exc_info=True)
                failure = exc
                if on_error is not None:
                    self.call_soon(lambda: on_error(failure))
                return
            self.call_soon(lambda: on_done(result))

        threading.Thread(target=runner, name="companion-ui-call", daemon=True).start()

    @Slot(object)
    def _on_snapshot(self, snapshot: CompanionSnapshot) -> None:
        # The dispatcher publishes in order, but `subscribe`'s first delivery may race with
        # it: never render an older snapshot over a newer one.
        if snapshot.rev < self._latest.rev:
            return
        self._latest = snapshot
        self.snapshot_changed.emit(snapshot)

    @Slot(object)
    def _on_notification(self, notification: Notification) -> None:
        self.notification_received.emit(notification)

    @Slot(object)
    def _on_call(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception:
            log.exception("GUI callback failed")
