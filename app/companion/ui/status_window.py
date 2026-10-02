"""The status window: what the tray shows, plus actions and the result lists.

Left click on the tray opens it; without a system tray (e.g. GNOME without the
AppIndicator extension) it is the main window, and closing it quits VoiceMate.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QColor, QFont, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.companion.contract import CompanionSnapshot, FlowEntry, Notification, RecentItem
from app.companion.ui import texts
from app.companion.ui.chords import display_chord
from app.companion.ui.icons import app_icon, state_pixmap
from app.companion.ui.shell import Shell
from app.companion.ui.tray import result_label
from app.companion.ui.view_model import (
    CANCELLABLE_STATES,
    RECORDING_STATES,
    can_trigger,
    chord_for,
    displayed_flows,
    idle_chord,
)
from app.i18n import _

HEADER_ICON_SIZE = 40
LIST_TEXT_LIMIT = 90
PENDING_VISIBLE_ROWS = 3
RECENT_MIN_ROWS = 3
_ITEM_ROLE = Qt.ItemDataRole.UserRole


class Banner(QFrame):
    """A tinted strip with a message and optional buttons (outdated engine, WSL restart...)."""

    def __init__(self, tone: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("banner")
        # Translucent tint and border: they blend with whatever the window color is, so the
        # banner keeps its contrast when the palette switches between light and dark.
        tint = QColor(tone)
        rgb = f"{tint.red()}, {tint.green()}, {tint.blue()}"
        self.setStyleSheet(
            f"QFrame#banner {{ background-color: rgba({rgb}, 0.16); border: 1px solid rgba({rgb}, 0.6);"
            " border-radius: 4px; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.label, 1)
        self.buttons = QHBoxLayout()
        layout.addLayout(self.buttons)

    def add_button(self, text: str) -> QPushButton:
        button = QPushButton(text)
        self.buttons.addWidget(button)
        return button


class StatusWindow(QWidget):
    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._snapshot = shell.bridge.snapshot
        self._flow_buttons: list[QPushButton] = []
        self._flow_key: tuple[tuple[str, str], ...] = ()
        self._results_key: tuple[object, ...] = (None,)
        self.setWindowTitle(texts.APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(460)
        self._build()
        self.show_snapshot(self._snapshot)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        self.state_icon = QLabel()
        self.state_icon.setFixedSize(HEADER_ICON_SIZE, HEADER_ICON_SIZE)
        header.addWidget(self.state_icon, 0, Qt.AlignmentFlag.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.status_label = QLabel()
        font = QFont(self.status_label.font())
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setWeight(QFont.Weight.DemiBold)
        self.status_label.setFont(font)
        self.status_label.setWordWrap(True)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        titles.addWidget(self.status_label)
        titles.addWidget(self.detail_label)
        header.addLayout(titles, 1)
        root.addLayout(header)

        self.outdated_banner = Banner("#F2A516")
        self.outdated_banner.label.setText(_("The engine is older than this app. Restart or update it."))
        self.outdated_restart = self.outdated_banner.add_button(_("Restart engine"))
        self.outdated_restart.clicked.connect(self._shell.controller.restart_engine)
        root.addWidget(self.outdated_banner)

        self.wsl_banner = Banner("#F2A516")
        self.wsl_banner.label.setText(
            _("WSL audio is not working. Restarting WSL fixes it, but it also stops every running distro.")
        )
        self.wsl_restart_button = self.wsl_banner.add_button(_("Restart WSL now"))
        self.wsl_restart_button.clicked.connect(lambda: self._shell.controller.answer_wsl_restart(True))
        self.wsl_later_button = self.wsl_banner.add_button(_("Not now"))
        self.wsl_later_button.clicked.connect(lambda: self._shell.controller.answer_wsl_restart(False))
        root.addWidget(self.wsl_banner)

        self.message_banner = Banner("#3B82F6")
        self.message_close = self.message_banner.add_button(_("Dismiss"))
        self.message_close.clicked.connect(self.message_banner.hide)
        self.message_banner.hide()
        root.addWidget(self.message_banner)

        self.actions_row = QHBoxLayout()
        self.actions_row.setSpacing(8)
        self.cancel_button = QPushButton(_("Cancel"))
        self.cancel_button.clicked.connect(self._shell.controller.cancel)
        self.actions_row.addWidget(self.cancel_button)
        self.actions_row.addStretch(1)
        root.addLayout(self.actions_row)

        info = QFormLayout()
        info.setHorizontalSpacing(16)
        info.setVerticalSpacing(4)
        self.engine_value = QLabel()
        self.audio_value = QLabel()
        self.mics_value = QLabel()
        self.restarts_value = QLabel()
        info.addRow(_("Engine:"), self.engine_value)
        info.addRow(_("Audio:"), self.audio_value)
        info.addRow(_("Microphones:"), self.mics_value)
        info.addRow(_("Restarts:"), self.restarts_value)
        root.addLayout(info)

        self.pending_group, self.pending_list, self.pending_copy = self._results_group(_("Not copied"))
        self.pending_group.setToolTip(_("Transcriptions that never reached the clipboard. Copy them from here."))
        root.addWidget(self.pending_group)
        self.recent_group, self.recent_list, self.recent_copy = self._results_group(_("Recent"))
        root.addWidget(self.recent_group, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.settings_button = QPushButton(_("Settings..."))
        self.settings_button.clicked.connect(self._shell.show_settings)
        self.restart_button = QPushButton(_("Restart engine"))
        self.restart_button.clicked.connect(self._shell.controller.restart_engine)
        self.logs_button = QPushButton(_("Open logs"))
        self.logs_button.clicked.connect(self._shell.controller.open_logs)
        self.quit_button = QPushButton(_("Quit VoiceMate"))
        self.quit_button.clicked.connect(self._shell.quit_app)
        for button in (self.settings_button, self.restart_button, self.logs_button):
            bottom.addWidget(button)
        bottom.addStretch(1)
        bottom.addWidget(self.quit_button)
        root.addLayout(bottom)

    def _results_group(self, title: str) -> tuple[QGroupBox, QListWidget, QPushButton]:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        results = QListWidget()
        results.setAccessibleName(title)
        results.setUniformItemSizes(True)
        results.setTextElideMode(Qt.TextElideMode.ElideRight)
        results.setMinimumHeight(64)
        results.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        copy = QPushButton(_("Copy"))
        copy.setEnabled(False)
        results.itemSelectionChanged.connect(lambda: copy.setEnabled(bool(results.selectedItems())))
        results.itemActivated.connect(self._copy_item)
        copy.clicked.connect(lambda: self._copy_selected(results))
        layout.addWidget(results)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(copy)
        layout.addLayout(row)
        return group, results, copy

    # ------------------------------------------------------------------ rendering

    def show_snapshot(self, snapshot: CompanionSnapshot) -> None:
        self._snapshot = snapshot
        settings = self._shell.settings()
        state = snapshot.tray_state
        self._render_icon()
        self.detail_label.setText(snapshot.detail)
        self.detail_label.setVisible(bool(snapshot.detail))
        self.outdated_banner.setVisible(snapshot.engine_outdated)
        self.wsl_banner.setVisible(snapshot.pending_wsl_restart)

        flows = displayed_flows(snapshot, settings)
        self._render_flow_buttons(snapshot, flows)
        self.cancel_button.setEnabled(state in CANCELLABLE_STATES)

        self.engine_value.setText(texts.supervisor_labels()[snapshot.supervisor])
        self.audio_value.setText(texts.audio_labels()[snapshot.audio])
        self.mics_value.setText(texts.mic_count_label(snapshot.mic_count))
        self.restarts_value.setText(str(snapshot.restarts))

        recent = self._shell.controller.recent_results()
        pending = self._shell.controller.pending_results()
        results_key = (_keys(recent), _keys(pending))
        if results_key != self._results_key:
            # Refill only on change: a refill would drop the user's selection.
            self._results_key = results_key
            self._fill(self.recent_list, recent, _("No transcriptions yet"))
            self._fill(self.pending_list, pending, "")
            self._fit_rows(self.pending_list, PENDING_VISIBLE_ROWS)
            # Recent never shrinks below a few rows: banners make the window grow instead.
            self.recent_list.setMinimumHeight(self._rows_height(self.recent_list, RECENT_MIN_ROWS))
        self.pending_group.setVisible(bool(pending))
        self.refresh_time()
        self._ensure_fits()

    def _render_icon(self) -> None:
        pixmap = state_pixmap(self._snapshot.tray_state, HEADER_ICON_SIZE, self.devicePixelRatioF())
        self.state_icon.setPixmap(pixmap)

    def _ensure_fits(self) -> None:
        """Grow (never shrink) to fit banners that just appeared. The layout's minimum size
        ignores the wrapped banner labels (height-for-width), so ask for the height at this width."""
        layout = self.layout()
        if layout is None or not self.isVisible():
            return
        layout.activate()
        needed = layout.totalHeightForWidth(self.width()) if layout.hasHeightForWidth() else 0
        needed = max(needed, layout.totalMinimumSize().height())
        if needed > self.height():
            self.resize(self.width(), needed)

    def refresh_time(self) -> None:
        chord = idle_chord(self._snapshot, self._shell.settings())
        self.status_label.setText(texts.status_line(self._snapshot, chord))

    def _render_flow_buttons(self, snapshot: CompanionSnapshot, flows: tuple[FlowEntry, ...]) -> None:
        """One button per flow. Rebuilt only when the flows change, otherwise relabeled in
        place, so the focus stays on the button (Space starts, Space again stops)."""
        key = tuple((flow.name, flow.kind) for flow in flows)
        if key != self._flow_key:
            self._flow_key = key
            for button in self._flow_buttons:
                self.actions_row.removeWidget(button)
                button.deleteLater()
            self._flow_buttons = []
            for index, flow in enumerate(flows):
                button = QPushButton()
                button.clicked.connect(lambda _checked=False, name=flow.name: self._shell.controller.toggle(name))
                self.actions_row.insertWidget(index, button)
                self._flow_buttons.append(button)
            # New buttons would come last in the tab order: put them before Cancel.
            chain = [self.message_close, *self._flow_buttons, self.cancel_button]
            for first, second in zip(chain, chain[1:], strict=False):
                QWidget.setTabOrder(first, second)
        settings = self._shell.settings()
        recording = snapshot.tray_state in RECORDING_STATES
        enabled = can_trigger(snapshot)
        for flow, button in zip(flows, self._flow_buttons, strict=True):
            button.setText(texts.flow_stop_title(flow.kind) if recording else texts.flow_title(flow, flows))
            button.setEnabled(enabled)
            chord = chord_for(flow, snapshot, settings)
            button.setToolTip(display_chord(chord) if chord else "")

    @property
    def flow_buttons(self) -> list[QPushButton]:
        return list(self._flow_buttons)

    def _fill(self, results: QListWidget, items: list[RecentItem], empty_text: str) -> None:
        results.clear()
        for item in items:
            row = QListWidgetItem(result_label(item, LIST_TEXT_LIMIT))
            row.setToolTip(texts.shorten(item.record["text"], 400))
            row.setData(_ITEM_ROLE, item)
            results.addItem(row)
        if not items and empty_text:
            placeholder = QListWidgetItem(empty_text)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            results.addItem(placeholder)
        if items:
            results.setCurrentRow(0)  # Copy works at once on the newest (or oldest pending) item

    @staticmethod
    def _rows_height(results: QListWidget, rows: int) -> int:
        row_height = results.sizeHintForRow(0) if results.count() else results.fontMetrics().height() + 8
        return int(row_height * rows + 2 * results.frameWidth() + 4)

    def _fit_rows(self, results: QListWidget, rows: int) -> None:
        """The pending list stays compact: as tall as its items, up to `rows` of them."""
        results.setMinimumHeight(0)
        results.setMaximumHeight(self._rows_height(results, min(max(results.count(), 1), rows)))

    def _copy_item(self, row: QListWidgetItem) -> None:
        item = row.data(_ITEM_ROLE)
        if isinstance(item, RecentItem):
            self._shell.copy_result(item)

    def _copy_selected(self, results: QListWidget) -> None:
        for row in results.selectedItems():
            self._copy_item(row)

    # ------------------------------------------------------------------ notifications / quitting

    def show_notification(self, notification: Notification) -> None:
        """Without a tray there are no balloons: the latest notification shows here."""
        title = f"<b>{_escape(notification.title)}</b>"
        self.message_banner.label.setText(f"{title}<br>{_escape(notification.message)}")
        self.message_banner.show()

    def set_quitting(self) -> None:
        self.status_label.setText(_("Quitting..."))
        for button in self.findChildren(QPushButton):
            button.setEnabled(False)

    # ------------------------------------------------------------------ window behavior

    def present(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self._ensure_fits()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._shell.has_tray:
            event.accept()  # hide; the tray keeps running
            return
        # Without a tray this window IS the app: closing it quits (the engine too).
        event.ignore()
        self._shell.quit_app()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._shell.has_tray:
            self.close()
            return
        super().keyPressEvent(event)


def _keys(items: list[RecentItem]) -> tuple[tuple[str, int], ...]:
    return tuple((item.instance, item.record["result_seq"]) for item in items)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
