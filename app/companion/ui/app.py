"""`CompanionUi`: wires the controller to the tray, the status window, the settings
window and the notifications, and owns the app-level flows (commands, quit)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from typing import Final

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon

from app.companion.contract import (
    CompanionController,
    CompanionSettings,
    CompanionSnapshot,
    Notification,
    RecentItem,
)
from app.companion.ui.bridge import ControllerBridge
from app.companion.ui.icons import app_icon
from app.companion.ui.notifications import NotificationPresenter
from app.companion.ui.settings_window import SettingsDialog
from app.companion.ui.status_window import StatusWindow
from app.companion.ui.tray import TrayIcon
from app.i18n import _

log = logging.getLogger(__name__)

# The controller calls on_done within 15 s; past this, quit anyway.
QUIT_SAFETY_MS: Final = 20_000
# Logoff/shutdown (aboutToQuit without our Quit): how long to wait for a clean stop.
SESSION_END_WAIT_S: Final = 8.0
CLOCK_MS: Final = 1000


class CompanionUi(QObject):
    """Implements `Shell` for the views."""

    quit_finished = Signal()

    def __init__(
        self,
        controller: CompanionController,
        *,
        tray_available: bool | None = None,
        exit_app: Callable[[], None] = QCoreApplication.quit,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._exit_app = exit_app
        self._bridge = ControllerBridge(controller, self)
        self._settings = controller.settings()
        self._has_tray = QSystemTrayIcon.isSystemTrayAvailable() if tray_available is None else tray_available
        self._quitting = False
        self._quit_done = False
        self._wsl_box: QMessageBox | None = None

        self.tray: TrayIcon | None = TrayIcon(self, self) if self._has_tray else None
        self.status_window = StatusWindow(self)
        self.settings_dialog: SettingsDialog | None = None
        self.notifications = NotificationPresenter(
            self.tray,
            self.status_window,
            {"show_status": self.show_status, "open_logs": controller.open_logs, "open_settings": self.show_settings},
            self,
        )
        self._bridge.snapshot_changed.connect(self._render)
        self._bridge.notification_received.connect(self.notifications.present)
        self._clock = QTimer(self)
        self._clock.setInterval(CLOCK_MS)
        self._clock.timeout.connect(self._tick)

    # ------------------------------------------------------------------ Shell

    @property
    def controller(self) -> CompanionController:
        return self._controller

    @property
    def bridge(self) -> ControllerBridge:
        return self._bridge

    @property
    def has_tray(self) -> bool:
        return self._has_tray

    @property
    def quitting(self) -> bool:
        return self._quitting

    def settings(self) -> CompanionSettings:
        return self._settings

    def settings_applied(self, settings: CompanionSettings) -> None:
        self._settings = settings
        self._render(self._bridge.snapshot)

    def show_status(self) -> None:
        if not self._quitting:
            self.status_window.present()

    def show_settings(self) -> None:
        if self._quitting:
            return
        if self.settings_dialog is None:
            self.settings_dialog = SettingsDialog(self)
            self.settings_dialog.present(reload=False)
            return
        self.settings_dialog.present(reload=not self.settings_dialog.isVisible())

    def confirm_wsl_restart(self) -> None:
        if self._wsl_box is not None:
            self._wsl_box.raise_()
            self._wsl_box.activateWindow()
            return
        box = QMessageBox(
            QMessageBox.Icon.Question,
            _("Restart WSL now?"),
            _("WSL audio is not working. Restarting WSL fixes it, but it also stops every running distro."),
        )
        box.setWindowIcon(app_icon())
        restart = box.addButton(_("Restart WSL"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Not now"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(restart)
        box.finished.connect(lambda _result: self._on_wsl_answer(box, box.clickedButton() is restart))
        self._wsl_box = box
        box.show()

    def _on_wsl_answer(self, box: QMessageBox, approved: bool) -> None:
        answered = self._wsl_box
        if answered is None or answered is not box:
            return  # closed by the app (the question went away), not answered
        self._wsl_box = None
        answered.deleteLater()
        self._controller.answer_wsl_restart(approved)

    def set_muted(self, muted: bool) -> None:
        updated = replace(self._settings, cues_enabled=not muted)
        controller = self._controller
        self._bridge.run_async(
            lambda: controller.apply_settings(updated),
            lambda errors: self._mute_applied(updated, errors),
            lambda exc: self._mute_applied(updated, [str(exc)]),
        )

    def _mute_applied(self, settings: CompanionSettings, errors: list[str]) -> None:
        if errors:
            self.notifications.present(Notification("error", _("Could not change the sounds"), errors[0]))
            self._render(self._bridge.snapshot)  # puts the menu check back
            return
        self.settings_applied(settings)

    def copy_result(self, item: RecentItem) -> None:
        self._controller.copy_result(item.instance, item.record["result_seq"])

    # ------------------------------------------------------------------ lifecycle

    def start(self, show_window: bool) -> None:
        """Subscribe and show the UI (the caller then starts the controller)."""
        self._bridge.attach()
        if self.tray is not None:
            self.tray.show()
        if show_window or self.tray is None:
            self.status_window.present()

    def handle_command(self, command: str) -> None:
        """A `CompanionCommand` forwarded by a second launch (jump list, pinned shortcut)."""
        log.info("command from another launch: %s", command)
        if command == "show":
            self.show_status()
        elif command == "settings":
            self.show_settings()
        elif command == "quit":
            self.quit_app()
        elif command == "restart-engine":
            self._controller.restart_engine()
        elif command == "restart-wsl":
            self._controller.restart_wsl()

    def quit_app(self) -> None:
        """Quit VoiceMate: the controller stops what it started, then the Qt loop ends."""
        if self._quitting:
            return
        self._quitting = True
        log.info("quitting")
        if self.settings_dialog is not None:
            self.settings_dialog.hotkeys_page.cancel_capture()
            self.settings_dialog.hide()
        if self._wsl_box is not None:
            box, self._wsl_box = self._wsl_box, None
            box.close()
        if self.tray is not None:
            self.tray.set_quitting()
        self.status_window.set_quitting()
        self._controller.quit(lambda: self._bridge.call_soon(self._finish_quit))
        QTimer.singleShot(QUIT_SAFETY_MS, self._finish_quit)

    def _finish_quit(self) -> None:
        if self._quit_done:
            return
        self._quit_done = True
        self._clock.stop()
        self._bridge.detach()
        if self.tray is not None:
            self.tray.hide()
        self.status_window.hide()
        self.quit_finished.emit()
        self._exit_app()

    def on_about_to_quit(self) -> None:
        """The Qt loop is ending without our Quit (e.g. the session ends): stop cleanly anyway."""
        if self._quitting:
            return
        self._quitting = True
        done = threading.Event()
        self._controller.quit(done.set)
        if not done.wait(SESSION_END_WAIT_S):
            log.warning("controller did not finish quitting in %.0f s", SESSION_END_WAIT_S)
        self._bridge.detach()

    # ------------------------------------------------------------------ rendering

    def _render(self, snapshot: CompanionSnapshot) -> None:
        if self._quitting:
            return
        if self.tray is not None:
            self.tray.show_snapshot(snapshot)
        self.status_window.show_snapshot(snapshot)
        if snapshot.tray_state == "recording" and snapshot.recording_since is not None:
            if not self._clock.isActive():
                self._clock.start()
        else:
            self._clock.stop()
        if not snapshot.pending_wsl_restart and self._wsl_box is not None:
            box, self._wsl_box = self._wsl_box, None
            box.close()
            box.deleteLater()

    def _tick(self) -> None:
        if self.tray is not None:
            self.tray.refresh_time()
        self.status_window.refresh_time()
