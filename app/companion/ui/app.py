"""`CompanionUi`: wires the controller to the tray, the status window, the settings window
and the notifications, and owns the app-level flows (commands, quit, restart)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Final, get_args

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QAbstractButton, QMessageBox, QSystemTrayIcon

from app.companion.contract import (
    CompanionCommand,
    CompanionController,
    CompanionSettings,
    CompanionSnapshot,
    Notification,
    RecentItem,
    UiLanguage,
)
from app.companion.ui.bridge import ControllerBridge
from app.companion.ui.icons import app_icon
from app.companion.ui.notifications import NotificationPresenter
from app.companion.ui.settings_window import SettingsDialog
from app.companion.ui.status_window import StatusWindow
from app.companion.ui.tray import TrayIcon
from app.i18n import _, active_language, catalog_for

log = logging.getLogger(__name__)

# The controller calls on_done within 15 s; past this, quit anyway.
QUIT_SAFETY_MS: Final = 20_000
# Logoff/shutdown (aboutToQuit without our Quit): how long to wait for a clean stop.
SESSION_END_WAIT_S: Final = 8.0
CLOCK_MS: Final = 1000
# Linux autostart: the panel's tray may appear after us; wait this long before
# falling back to the window.
TRAY_WAIT_S: Final = 15.0
TRAY_POLL_MS: Final = 500
COMMANDS: Final[tuple[CompanionCommand, ...]] = get_args(CompanionCommand)

# Reads the applied settings to rebuild the taskbar jump list (main.py passes it on Windows).
JumpListUpdater = Callable[[CompanionSettings], None]
# Starts a new detached companion that takes over once this one quits (main.py passes
# `app.companion.relaunch.relaunch_companion`); raises when it cannot start.
Relauncher = Callable[[], None]
# The catalog a language setting selects (`app.i18n.catalog_for`).
LanguageCatalog = Callable[[UiLanguage], str]


class CompanionUi(QObject):
    """Implements `Shell` for the views."""

    quit_started = Signal()
    quit_finished = Signal()

    def __init__(
        self,
        controller: CompanionController,
        *,
        tray_available: bool | None = None,
        tray_probe: Callable[[], bool] = QSystemTrayIcon.isSystemTrayAvailable,
        exit_app: Callable[[], None] = QCoreApplication.quit,
        update_jump_list: JumpListUpdater | None = None,
        relaunch: Relauncher | None = None,
        language_catalog: LanguageCatalog = catalog_for,
        running_catalog: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._exit_app = exit_app
        self._tray_probe = tray_probe
        self._update_jump_list = update_jump_list
        self._relaunch = relaunch
        self._language_catalog = language_catalog
        # The catalog this UI was built with (main.py calls set_language before the UI).
        self._running_catalog = running_catalog if running_catalog is not None else active_language()
        self._bridge = ControllerBridge(controller, self)
        self._quitting = False
        self._quit_done = False
        self._wsl_box: QMessageBox | None = None
        self._restart_wsl_box: QMessageBox | None = None
        self._language_box: QMessageBox | None = None
        self._clear_pending_box: QMessageBox | None = None
        self._jump_list_mode: str | None = None

        self.tray: TrayIcon | None = None
        self.status_window = StatusWindow(self)
        self.settings_dialog: SettingsDialog | None = None
        self.notifications = NotificationPresenter(
            None,
            self.status_window,
            {"show_status": self.show_status, "open_logs": controller.open_logs, "open_settings": self.show_settings},
            self,
        )
        if tray_available if tray_available is not None else tray_probe():
            self._create_tray()
        self._bridge.snapshot_changed.connect(self._render)
        self._bridge.notification_received.connect(self._present_notification)
        self._clock = QTimer(self)
        self._clock.setInterval(CLOCK_MS)
        self._clock.timeout.connect(self._tick)
        self._tray_wait = QTimer(self)
        self._tray_wait.setInterval(TRAY_POLL_MS)
        self._tray_wait.timeout.connect(self._poll_tray)
        self._tray_wait_deadline = 0.0

    # ------------------------------------------------------------------ Shell

    @property
    def controller(self) -> CompanionController:
        return self._controller

    @property
    def bridge(self) -> ControllerBridge:
        return self._bridge

    @property
    def has_tray(self) -> bool:
        return self.tray is not None

    @property
    def quitting(self) -> bool:
        return self._quitting

    def settings(self) -> CompanionSettings:
        # Never cached: the core also changes settings (client_key, a detected engine_dir).
        return self._controller.settings()

    def settings_changed(self) -> None:
        self._render(self._bridge.snapshot)
        self._refresh_jump_list()

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
        box, restart = _question(
            _("Restart WSL now?"),
            _("WSL audio is not working. Restarting WSL fixes it, but it also stops every running distro."),
            _("Restart WSL"),
            _("Not now"),
        )
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

    def confirm_restart_wsl(self) -> None:
        if self._quitting or self.settings().engine_mode != "wsl2":
            log.info("Restart WSL ignored: not in WSL mode")
            return
        if self._restart_wsl_box is not None:
            self._restart_wsl_box.raise_()
            self._restart_wsl_box.activateWindow()
            return
        box, restart = _question(
            _("Restart WSL?"),
            _("This stops every running WSL distro, Docker included, and starts the engine again."),
            _("Restart WSL"),
            _("Cancel"),
        )

        def answered(_result: int) -> None:
            self._restart_wsl_box = None
            box.deleteLater()
            if box.clickedButton() is restart and not self._quitting:
                self._controller.restart_wsl()

        box.finished.connect(answered)
        self._restart_wsl_box = box
        box.show()

    def set_muted(self, muted: bool) -> None:
        controller = self._controller

        def apply() -> list[str]:
            # Read the live settings in the worker: only cues_enabled changes.
            return controller.apply_settings(replace(controller.settings(), cues_enabled=not muted))

        self._bridge.run_async(apply, self._mute_applied, lambda exc: self._mute_applied([str(exc)]))

    def _mute_applied(self, errors: list[str]) -> None:
        if errors:
            self._present_notification(Notification("error", _("Could not change the sounds"), errors[0]))
        self.settings_changed()  # the menu check follows what was actually applied

    def copy_result(self, item: RecentItem) -> None:
        self._controller.copy_result(item.instance, item.record["result_seq"])

    def language_needs_restart(self, language: UiLanguage) -> bool:
        return self._language_catalog(language) != self._running_catalog

    def offer_language_restart(self) -> None:
        if self._quitting:
            return
        if self._language_box is not None:
            self._language_box.raise_()
            self._language_box.activateWindow()
            return
        box, restart = _question(
            _("Restart VoiceMate?"),
            _("VoiceMate needs to restart to change the language. Restart now?"),
            _("Restart now"),
            _("Later"),
        )
        box.setWindowModality(Qt.WindowModality.ApplicationModal)

        def answered(_result: int) -> None:
            current = self._language_box
            if current is None or current is not box:
                return  # closed by quit, not answered
            self._language_box = None
            current.deleteLater()
            if current.clickedButton() is restart:
                self.restart_app()

        box.finished.connect(answered)
        self._language_box = box
        box.show()
        box.raise_()
        box.activateWindow()

    def restart_app(self) -> None:
        """Restart VoiceMate: start the new instance first, then quit like "Quit VoiceMate"
        (the engine this instance started stops and starts again with the new one). When
        the new instance cannot start, say so and keep running."""
        if self._quitting:
            return
        try:
            if self._relaunch is None:
                raise OSError("restarting is not available here")
            self._relaunch()
        except Exception:
            log.exception("could not start a new instance; not restarting")
            self._present_notification(
                Notification(
                    "error",
                    _("Could not restart VoiceMate"),
                    _("Quit VoiceMate and start it again to change the language."),
                )
            )
            return
        log.info("restarting: the new instance takes over once this one has quit")
        self.quit_app()

    def confirm_clear_pending(self) -> None:
        if self._quitting:
            return
        if self._clear_pending_box is not None:
            self._clear_pending_box.raise_()
            self._clear_pending_box.activateWindow()
            return
        # What the user sees now: an item that fails meanwhile is not cleared unseen.
        keys = tuple((item.instance, item.record["result_seq"]) for item in self._controller.pending_results())
        box, clear = _question(
            _('Clear the "Not copied" list?'),
            _("These transcriptions never reached the clipboard. Once cleared, they cannot be copied any more."),
            _("Clear list"),
            _("Cancel"),
        )

        def answered(_result: int) -> None:
            self._clear_pending_box = None
            box.deleteLater()
            if box.clickedButton() is clear and not self._quitting:
                self._controller.clear_pending(keys)

        box.finished.connect(answered)
        self._clear_pending_box = box
        box.show()

    # ------------------------------------------------------------------ lifecycle

    def start(self, show_window: bool, wait_for_tray: bool = False) -> None:
        """Subscribe and show the UI (the caller then starts the controller).

        `wait_for_tray`: started at login without a tray yet (Linux panels load after us):
        poll for it before falling back to the window.
        """
        self._bridge.attach()
        self._refresh_jump_list()
        if self.tray is not None:
            self.tray.show()
            if show_window:
                self.status_window.present()
            return
        if wait_for_tray and not show_window:
            self._tray_wait_deadline = time.monotonic() + TRAY_WAIT_S
            self._tray_wait.start()
            return
        self.status_window.present()

    def _poll_tray(self) -> None:
        if self._quitting:
            self._tray_wait.stop()
            return
        if self._tray_probe():
            self._tray_wait.stop()
            self._create_tray()
            if self.tray is not None:
                self.tray.show()
            return
        if time.monotonic() >= self._tray_wait_deadline:
            self._tray_wait.stop()
            log.info("no system tray after %.0f s: the status window is the main window", TRAY_WAIT_S)
            self.status_window.present()

    def _create_tray(self) -> None:
        if self.tray is not None:
            return
        self.tray = TrayIcon(self, self)
        self.notifications.set_tray(self.tray)

    def handle_command(self, command: CompanionCommand) -> None:
        """A command forwarded by a second launch (jump list, pinned shortcut)."""
        if command not in COMMANDS:
            log.warning("unknown command ignored: %r", command)
            return
        if self._quitting:
            log.info("command %s ignored: quitting", command)
            return
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
            self.confirm_restart_wsl()

    def quit_app(self) -> None:
        """Quit VoiceMate: the controller stops what it started, then the Qt loop ends."""
        if self._quitting:
            return
        self._quitting = True
        log.info("quitting")
        # Safety net first: even if quit() never calls back (or raises), the app ends.
        QTimer.singleShot(QUIT_SAFETY_MS, self._finish_quit)
        self.quit_started.emit()
        self._tray_wait.stop()
        if self.settings_dialog is not None:
            self.settings_dialog.hotkeys_page.cancel_capture()
            self.settings_dialog.hide()
        for attribute in ("_wsl_box", "_restart_wsl_box", "_language_box", "_clear_pending_box"):
            box = getattr(self, attribute)
            if box is not None:
                setattr(self, attribute, None)
                box.close()
                box.deleteLater()
        if self.tray is not None:
            self.tray.set_quitting()
        self.status_window.set_quitting()
        try:
            self._controller.quit(lambda: self._bridge.call_soon(self._finish_quit))
        except Exception:
            log.exception("controller.quit failed; quitting at once")
            self._finish_quit()

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
        # Deferred: a quit buffered during startup can finish before app.exec() runs, and a
        # QCoreApplication.quit() made before the loop starts is silently dropped. A
        # zero-delay timer fires once the loop runs (or at once, if it already does).
        QTimer.singleShot(0, self._exit_app)

    def on_about_to_quit(self) -> None:
        """The Qt loop is ending without our Quit (e.g. the session ends): stop cleanly anyway."""
        if self._quitting:
            return
        self._quitting = True
        self.quit_started.emit()
        done = threading.Event()
        try:
            self._controller.quit(done.set)
        except Exception:
            log.exception("controller.quit failed at session end")
            done.set()
        if not done.wait(SESSION_END_WAIT_S):
            log.warning("controller did not finish quitting in %.0f s", SESSION_END_WAIT_S)
        self._bridge.detach()

    # ------------------------------------------------------------------ rendering

    def _present_notification(self, notification: Notification) -> None:
        if self._quitting:
            return
        self.notifications.present(notification)

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

    def _refresh_jump_list(self) -> None:
        """The jump list offers Restart WSL only in WSL mode: rebuild it when the mode changes."""
        if self._update_jump_list is None:
            return
        settings = self.settings()
        if settings.engine_mode == self._jump_list_mode:
            return
        self._jump_list_mode = settings.engine_mode
        try:
            self._update_jump_list(settings)
        except Exception:
            log.warning("could not update the jump list", exc_info=True)


def _question(title: str, text: str, accept: str, reject: str) -> tuple[QMessageBox, QAbstractButton]:
    """A question shown with show() (never exec()), with our own (translated) button texts;
    the caller picks the modality. The default (Enter) is `reject`: restarting stops things
    (every WSL distro, the engine) and clearing the Not copied list cannot be undone, so they
    must be an explicit click."""
    box = QMessageBox(QMessageBox.Icon.Question, title, text)
    box.setWindowIcon(app_icon())
    accept_button = box.addButton(accept, QMessageBox.ButtonRole.AcceptRole)
    reject_button = box.addButton(reject, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(reject_button)
    box.setEscapeButton(reject_button)
    return box, accept_button
