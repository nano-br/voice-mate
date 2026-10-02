"""The tray icon and its menu (docs/companion-app.md, "Tray states, cues and reactions")."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app.companion.contract import CompanionSnapshot, FlowEntry, NotificationLevel, RecentItem, TrayState
from app.companion.ui import texts
from app.companion.ui.chords import display_chord
from app.companion.ui.icons import GlyphTone, taskbar_glyph_tone, tray_icon
from app.companion.ui.shell import Shell
from app.companion.ui.view_model import (
    CANCELLABLE_STATES,
    RECORDING_STATES,
    can_trigger,
    chord_for,
    displayed_flows,
    idle_chord,
    settings_read_only,
)
from app.i18n import _

THEME_POLL_MS = 4000
MENU_TEXT_LIMIT = 48
_MESSAGE_ICONS = {
    "info": QSystemTrayIcon.MessageIcon.Information,
    "warning": QSystemTrayIcon.MessageIcon.Warning,
    "error": QSystemTrayIcon.MessageIcon.Critical,
}


def menu_text(text: str) -> str:
    """Menu items treat "&" as a mnemonic marker: show it literally."""
    return text.replace("&", "&&")


class TrayIcon(QObject):
    """Owns the QSystemTrayIcon; renders snapshots; menu actions call the controller or the shell."""

    message_clicked = Signal()

    def __init__(self, shell: Shell, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._snapshot = shell.bridge.snapshot
        self._tone: GlyphTone = taskbar_glyph_tone()
        self._icons: dict[tuple[TrayState, GlyphTone], QIcon] = {}
        self._flow_key: tuple[tuple[str, str], ...] = ()
        self._flow_actions: list[QAction] = []
        self._quitting = False

        self.tray = QSystemTrayIcon(self)
        self.menu = QMenu()
        self.menu.setToolTipsVisible(True)
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self.message_clicked)

        # The taskbar theme can change at any time; reading one registry value is cheap.
        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(THEME_POLL_MS)
        self._theme_timer.timeout.connect(self._refresh_tone)
        self._theme_timer.start()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._refresh_tone)
        self.show_snapshot(self._snapshot)

    # ------------------------------------------------------------------ building

    def _build_menu(self) -> None:
        menu = self.menu
        self.status_action = menu.addAction("")
        self.status_action.setEnabled(False)
        self.open_action = menu.addAction(_("Open VoiceMate"))
        self.open_action.triggered.connect(self._shell.show_status)
        self._flows_separator = menu.addSeparator()
        self.cancel_action = menu.addAction(_("Cancel"))
        self.cancel_action.triggered.connect(self._shell.controller.cancel)
        menu.addSeparator()

        self.recent_menu = menu.addMenu(_("Recent"))
        self.recent_menu.setToolTipsVisible(True)
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.pending_menu = menu.addMenu(_("Not copied"))
        self.pending_menu.setToolTipsVisible(True)
        self.pending_menu.aboutToShow.connect(self._fill_pending)
        self.wsl_restart_action = menu.addAction(_("Restart WSL now..."))
        self.wsl_restart_action.triggered.connect(self._shell.confirm_wsl_restart)
        menu.addSeparator()

        self.mute_action = menu.addAction(_("Mute sounds"))
        self.mute_action.setCheckable(True)
        self.mute_action.triggered.connect(self._shell.set_muted)
        self.engine_menu = menu.addMenu(_("Engine"))
        self.restart_engine_action = self.engine_menu.addAction(_("Restart engine"))
        self.restart_engine_action.triggered.connect(self._shell.controller.restart_engine)
        self.restart_wsl_action = self.engine_menu.addAction(_("Restart WSL..."))
        self.restart_wsl_action.triggered.connect(self._shell.confirm_restart_wsl)
        self.engine_menu.addSeparator()
        self.open_logs_action = self.engine_menu.addAction(_("Open logs"))
        self.open_logs_action.triggered.connect(self._shell.controller.open_logs)
        self.settings_action = menu.addAction(_("Settings..."))
        self.settings_action.triggered.connect(self._shell.show_settings)
        menu.addSeparator()
        self.quit_action = menu.addAction(_("Quit VoiceMate"))
        self.quit_action.triggered.connect(self._shell.quit_app)
        # Settings do not come with snapshots: refresh what depends on them before showing.
        menu.aboutToShow.connect(self._before_menu_shows)

    def _render_flow_actions(self, flows: tuple[FlowEntry, ...], snapshot: CompanionSnapshot) -> None:
        """One action per flow. Rebuilt only when the flows change; labels update in place."""
        key = tuple((flow.name, flow.kind) for flow in flows)
        if key != self._flow_key:
            self._flow_key = key
            for action in self._flow_actions:
                self.menu.removeAction(action)
                action.deleteLater()
            self._flow_actions = []
            for flow in flows:
                action = QAction(self.menu)
                action.setData(flow.name)
                action.triggered.connect(lambda _checked=False, name=flow.name: self._shell.controller.toggle(name))
                self.menu.insertAction(self.cancel_action, action)
                self._flow_actions.append(action)
        settings = self._shell.settings()  # live: the chords may have just changed
        recording = snapshot.tray_state in RECORDING_STATES
        enabled = can_trigger(snapshot)  # also for actions just created (QAction starts enabled)
        for flow, action in zip(flows, self._flow_actions, strict=True):
            title = texts.flow_stop_title(flow.kind) if recording else texts.flow_title(flow, flows)
            chord = chord_for(flow, snapshot, settings)
            action.setText(menu_text(title) + (f"\t{display_chord(chord)}" if chord else ""))
            action.setEnabled(enabled)

    # ------------------------------------------------------------------ rendering

    @property
    def flow_actions(self) -> list[QAction]:
        return list(self._flow_actions)

    def show_snapshot(self, snapshot: CompanionSnapshot) -> None:
        self._snapshot = snapshot
        settings = self._shell.settings()
        flows = displayed_flows(snapshot, settings)
        self._render_flow_actions(flows, snapshot)
        self.cancel_action.setEnabled(snapshot.tray_state in CANCELLABLE_STATES)
        pending = snapshot.pending_unacked
        self.pending_menu.setTitle(_("Not copied ({count})").format(count=pending) if pending else _("Not copied"))
        self.pending_menu.menuAction().setVisible(pending > 0)
        self.wsl_restart_action.setVisible(snapshot.pending_wsl_restart)
        self._refresh_settings_items()
        self.tray.setIcon(self._icon_for(snapshot.tray_state))
        self.refresh_time()

    def refresh_time(self) -> None:
        """Re-render the time-dependent texts (the recording timer)."""
        snapshot = self._snapshot
        chord = idle_chord(snapshot, self._shell.settings())
        self.status_action.setText(menu_text(texts.status_line(snapshot, chord)))
        self.tray.setToolTip(texts.tooltip(snapshot, chord))

    def _refresh_settings_items(self) -> None:
        """What depends on the settings, read live (also right before the menu shows)."""
        settings = self._shell.settings()
        self.mute_action.setChecked(not settings.cues_enabled)
        self.mute_action.setEnabled(not settings_read_only(settings))
        self.restart_wsl_action.setVisible(settings.engine_mode == "wsl2")

    def _before_menu_shows(self) -> None:
        if self._quitting:
            return
        self._refresh_settings_items()
        self._render_flow_actions(displayed_flows(self._snapshot, self._shell.settings()), self._snapshot)

    def _icon_for(self, state: TrayState) -> QIcon:
        key = (state, self._tone)
        icon = self._icons.get(key)
        if icon is None:
            icon = self._icons[key] = tray_icon(state, self._tone)
        return icon

    def _refresh_tone(self) -> None:
        tone = taskbar_glyph_tone()
        if tone != self._tone:
            self._tone = tone
            self.tray.setIcon(self._icon_for(self._snapshot.tray_state))

    # ------------------------------------------------------------------ submenus

    def _fill_recent(self) -> None:
        self._fill_results(self.recent_menu, self._shell.controller.recent_results(), _("No transcriptions yet"))

    def _fill_pending(self) -> None:
        items = self._shell.controller.pending_results()
        self._fill_results(self.pending_menu, items, _("Nothing to copy"))
        if items:
            self.pending_menu.addSeparator()
            clear = self.pending_menu.addAction(_("Clear list"))
            clear.triggered.connect(lambda _checked=False: self._shell.confirm_clear_pending())

    def _fill_results(self, menu: QMenu, items: list[RecentItem], empty_text: str) -> None:
        menu.clear()
        if not items:
            placeholder = menu.addAction(empty_text)
            placeholder.setEnabled(False)
            return
        for item in items:
            action = menu.addAction(menu_text(result_label(item, MENU_TEXT_LIMIT)))
            action.setToolTip(texts.shorten(item.record["text"], 400))
            action.triggered.connect(lambda _checked=False, chosen=item: self._shell.copy_result(chosen))

    # ------------------------------------------------------------------ misc

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def supports_messages(self) -> bool:
        return bool(self.tray.isVisible() and QSystemTrayIcon.supportsMessages())

    def show_message(self, title: str, message: str, level: NotificationLevel) -> None:
        self.tray.showMessage(title, message, _MESSAGE_ICONS[level], 10_000)

    def set_quitting(self) -> None:
        self._quitting = True
        self.tray.setToolTip(f"{texts.APP_NAME}\n{_('Quitting...')}")
        for action in self.menu.actions():
            action.setEnabled(False)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._shell.show_status()


def result_label(item: RecentItem, limit: int) -> str:
    """One line for a result: its text, marked when it is Claude's answer."""
    text = texts.shorten(item.record["text"], limit)
    if item.record["kind"] == "ai_response":
        return _("Claude: {text}").format(text=text)
    return text
