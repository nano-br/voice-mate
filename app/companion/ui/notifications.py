"""Notifications from the controller -> tray balloons (or the status window without a tray).

Balloons have no buttons and only the LAST one is clickable, so the presenter
keeps the action of the last notification shown and runs it on click
(`NotificationAction`). Rate limiting and `notify_level` filtering are done by
the controller: everything that arrives here is shown.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from app.companion.contract import Notification, NotificationAction
from app.companion.ui.status_window import StatusWindow
from app.companion.ui.tray import TrayIcon


class NotificationPresenter(QObject):
    def __init__(
        self,
        tray: TrayIcon | None,
        status_window: StatusWindow,
        actions: dict[NotificationAction, Callable[[], None]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._tray: TrayIcon | None = None
        self._status_window = status_window
        self._actions = actions
        self._last_action: NotificationAction = "none"
        self.shown: list[Notification] = []  # newest last; for tests and diagnostics (bounded)
        self.set_tray(tray)

    def set_tray(self, tray: TrayIcon | None) -> None:
        """The tray can appear after startup (Linux panels at login)."""
        if tray is self._tray:
            return
        if self._tray is not None:
            self._tray.message_clicked.disconnect(self.on_clicked)
        self._tray = tray
        if tray is not None:
            tray.message_clicked.connect(self.on_clicked)

    @property
    def last_action(self) -> NotificationAction:
        return self._last_action

    def present(self, notification: Notification) -> None:
        self._last_action = notification.action
        self.shown = [*self.shown[-19:], notification]
        if self._tray is not None and self._tray.supports_messages():
            self._tray.show_message(notification.title, notification.message, notification.level)
            return
        # No tray (or no balloons): the banner in the status window. Never steal the focus;
        # flash the taskbar entry instead when something went wrong.
        self._status_window.show_notification(notification)
        if not self._status_window.isVisible():
            self._status_window.show()
        if notification.level != "info":
            QApplication.alert(self._status_window)

    def on_clicked(self) -> None:
        action = self._actions.get(self._last_action)
        if action is not None:
            action()
