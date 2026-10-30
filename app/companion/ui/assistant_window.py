"""The assistant's text screen: the last messages of its conversation and a box to type to it.

The window follows the assistant service's snapshots (published from the service's worker
thread, hopped to the GUI thread with the bridge). Enter sends, Shift+Enter adds a line; the box
is disabled, with the reason written under it, while the assistant is off, starting, answering
or broken. The service decides what typed text means (a voice command or a prompt).

In direct mode a banner names the conversation every prompt goes to, with a button to come
back. Answers of other conversations held for later are listed under the messages (title and
time; a click shows the whole answer), with "Hear all" and "Clear"; "Waiting for" lists the
requests the assistant is waiting on (its follow_up actions: title, reason and since when).

The messages are rebuilt only when they changed, and the view stays where the person scrolled
unless it was at the end; the box gets the focus back when it is enabled again.

The microphone button does what the talk hotkey does (record, then stop and send the text to the
assistant), and the line beside it follows VoiceMate's own state with the same badge and words as
the status window, so a recording started with the hotkey shows here too.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from html import escape

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.companion.assistant.contract import (
    AssistantService,
    AssistantSnapshot,
    ChatMessage,
    InboxEntry,
    PendingEntry,
    Unsubscribe,
)
from app.companion.contract import CompanionSnapshot
from app.companion.ui import texts
from app.companion.ui.chords import display_chord
from app.companion.ui.icons import app_icon, state_pixmap
from app.companion.ui.shell import Shell
from app.companion.ui.view_model import CANCELLABLE_STATES, RECORDING_STATES, can_trigger
from app.i18n import _

INPUT_ROWS = 3
STATE_ICON_SIZE = 20


class PromptEdit(QPlainTextEdit):
    """Enter submits, Shift+Enter inserts a line break."""

    def __init__(self, on_submit: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_submit = on_submit

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._on_submit()
            return
        super().keyPressEvent(event)


def message_label(message: ChatMessage) -> str:
    if message.role == "user":
        return _("You")
    if message.role == "assistant":
        return _("Assistant")
    return _("VoiceMate")


class AssistantWindow(QWidget):
    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._service: AssistantService = shell.controller.assistant()
        self._snapshot = self._service.snapshot()
        self._unsubscribe: Unsubscribe | None = None
        self._quitting = False
        self._shown_html = ""
        self._companion: CompanionSnapshot = shell.controller.snapshot()
        self.setWindowTitle(_("{app} assistant").format(app=texts.APP_NAME))
        self.setWindowIcon(app_icon())
        self.resize(520, 560)
        self._build()
        self._render(self._snapshot)
        self.show_companion(self._companion)
        self._unsubscribe = self._service.subscribe(self._on_snapshot)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        header = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setOpenExternalLinks(True)
        self.status_label.setTextFormat(Qt.TextFormat.RichText)
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        header.addWidget(self.status_label, 1)
        self.restart_button = QPushButton(_("Restart"))
        self.restart_button.clicked.connect(self._service.restart)
        header.addWidget(self.restart_button)
        self.back_button = QPushButton(_("Back to the assistant"))
        self.back_button.clicked.connect(self._service.follow_assistant)
        header.addWidget(self.back_button)
        root.addLayout(header)

        self.direct_banner = QLabel()
        self.direct_banner.setWordWrap(True)
        self.direct_banner.setStyleSheet("padding: 6px; border-radius: 4px; background: palette(alternate-base);")
        root.addWidget(self.direct_banner)

        self.messages = QTextBrowser()
        self.messages.setOpenLinks(False)
        self.messages.setAccessibleName(_("Messages"))
        root.addWidget(self.messages, 1)

        self.inbox_box = QGroupBox(_("Answers waiting"))
        inbox = QVBoxLayout(self.inbox_box)
        self.inbox_list = QListWidget()
        self.inbox_list.setAccessibleName(_("Answers waiting"))
        self.inbox_list.setMaximumHeight(self.inbox_list.fontMetrics().lineSpacing() * 4 + 12)
        self.inbox_list.currentItemChanged.connect(lambda current, _previous: self._show_answer(current))
        inbox.addWidget(self.inbox_list)
        self.inbox_text = QTextBrowser()
        self.inbox_text.setAccessibleName(_("The selected answer"))
        self.inbox_text.setMaximumHeight(self.inbox_text.fontMetrics().lineSpacing() * 6 + 12)
        self.inbox_text.hide()
        inbox.addWidget(self.inbox_text)
        inbox_buttons = QHBoxLayout()
        inbox_buttons.addStretch(1)
        self.hear_button = QPushButton(_("Hear all"))
        self.hear_button.clicked.connect(self._service.hear_ready)
        inbox_buttons.addWidget(self.hear_button)
        self.clear_inbox_button = QPushButton(_("Clear"))
        self.clear_inbox_button.clicked.connect(self._service.clear_inbox)
        inbox_buttons.addWidget(self.clear_inbox_button)
        inbox.addLayout(inbox_buttons)
        root.addWidget(self.inbox_box)

        self.pending_box = QGroupBox(_("Waiting for"))
        pending = QVBoxLayout(self.pending_box)
        self.pending_list = QListWidget()
        self.pending_list.setAccessibleName(_("Waiting for"))
        self.pending_list.setMaximumHeight(self.pending_list.fontMetrics().lineSpacing() * 4 + 12)
        pending.addWidget(self.pending_list)
        root.addWidget(self.pending_box)

        self.input = PromptEdit(self._submit)
        self.input.setAccessibleName(_("Message to the assistant"))
        self.input.setPlaceholderText(_("Message to the assistant (Enter sends, Shift+Enter adds a line)"))
        self.input.setFixedHeight(self.input.fontMetrics().lineSpacing() * INPUT_ROWS + 16)
        root.addWidget(self.input)

        footer = QHBoxLayout()
        self.reason_label = QLabel()
        self.reason_label.setWordWrap(True)
        self.reason_label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        footer.addWidget(self.reason_label, 1)
        self.mic_button = QPushButton()
        self.mic_button.clicked.connect(self._shell.controller.talk)
        footer.addWidget(self.mic_button)
        self.send_button = QPushButton(_("Send"))
        self.send_button.clicked.connect(self._submit)
        footer.addWidget(self.send_button)
        root.addLayout(footer)

        voice_row = QHBoxLayout()
        voice_row.setSpacing(6)
        self.mic_icon = QLabel()
        self.mic_icon.setFixedSize(STATE_ICON_SIZE, STATE_ICON_SIZE)
        voice_row.addWidget(self.mic_icon)
        self.mic_status = QLabel()
        self.mic_status.setWordWrap(True)
        self.mic_status.setAccessibleName(_("Microphone"))
        voice_row.addWidget(self.mic_status, 1)
        root.addLayout(voice_row)

    # ------------------------------------------------------------------ rendering

    def _on_snapshot(self, snapshot: AssistantSnapshot) -> None:
        """Service worker thread: hop to the GUI thread."""
        self._shell.bridge.call_soon(lambda: self._render_newer(snapshot))

    def _render_newer(self, snapshot: AssistantSnapshot) -> None:
        if not self._quitting and snapshot.rev >= self._snapshot.rev:
            self._render(snapshot)

    def _render(self, snapshot: AssistantSnapshot) -> None:
        self._snapshot = snapshot
        status = escape(texts.assistant_state_line(snapshot))
        if snapshot.followed_title:
            status += " | " + escape(_("Showing: {title}").format(title=snapshot.followed_title))
        if snapshot.session_url:
            status += ' | <a href="{url}">{text}</a>'.format(
                url=escape(snapshot.session_url), text=escape(_("Open on claude.ai"))
            )
        self.status_label.setText(status)
        self.restart_button.setVisible(snapshot.state in ("error", "stopped"))
        self.back_button.setVisible(bool(snapshot.followed_title or snapshot.direct_title))
        self.direct_banner.setVisible(bool(snapshot.direct_title))
        self.direct_banner.setText(
            _(
                'Talking directly to "{title}": what you say goes there as it is. Say "back to the '
                'assistant" or press the button to come back.'
            ).format(title=snapshot.direct_title)
            if snapshot.direct_title
            else ""
        )
        self._render_inbox(snapshot.inbox)
        self._render_pending(snapshot.pending)
        self._render_messages(snapshot)
        reason = texts.assistant_input_block(snapshot)
        enabled = not reason
        was_enabled = self.input.isEnabled()
        self.input.setEnabled(enabled)
        if enabled and not was_enabled and self.isActiveWindow():
            self.input.setFocus()  # disabling took the focus away while the assistant answered
        self.send_button.setEnabled(enabled)
        self.reason_label.setText(reason)
        self.reason_label.setVisible(bool(reason))

        self._render_mic()

    def show_companion(self, snapshot: CompanionSnapshot) -> None:
        """VoiceMate's own state (GUI thread): recording, transcribing and the rest."""
        self._companion = snapshot
        self._render_mic()

    def refresh_time(self) -> None:
        """Each second while recording: the elapsed time beside the microphone."""
        self.mic_status.setText(texts.status_line(self._companion))

    def _render_mic(self) -> None:
        companion = self._companion
        recording = companion.tray_state in RECORDING_STATES
        self.mic_button.setText(_("Stop and send") if recording else _("Talk"))
        # Stopping is always offered; starting needs the engine free (not still transcribing the
        # last recording) and an assistant that takes prompts.
        free = can_trigger(companion) and companion.tray_state not in CANCELLABLE_STATES
        self.mic_button.setEnabled(recording or (free and self._snapshot.state == "ready"))
        chord = self._shell.settings().assistant.talk_hotkey
        title = _("Stop recording and send it to the assistant") if recording else _("Talk to the assistant")
        self.mic_button.setToolTip(f"{title} ({display_chord(chord)})" if chord else title)
        self.mic_button.setAccessibleName(title)
        self.refresh_time()
        self._render_mic_icon()

    def _render_mic_icon(self) -> None:
        glyph = self.palette().color(QPalette.ColorRole.WindowText)
        self.mic_icon.setPixmap(
            state_pixmap(self._companion.tray_state, glyph, STATE_ICON_SIZE, self.devicePixelRatioF())
        )

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "mic_icon"):
            self._render_mic_icon()

    def _render_messages(self, snapshot: AssistantSnapshot) -> None:
        """Only when the messages changed: a rebuild loses the selection and the scroll."""
        html = self._messages_html(snapshot)
        if html == self._shown_html:
            return
        bar = self.messages.verticalScrollBar()
        at_end = self._shown_html == "" or bar.value() >= bar.maximum() - 4
        kept = bar.value()
        self._shown_html = html
        self.messages.setHtml(html)
        bar.setValue(bar.maximum() if at_end else min(kept, bar.maximum()))

    @staticmethod
    def _messages_html(snapshot: AssistantSnapshot) -> str:
        parts: list[str] = []
        for message in snapshot.messages:
            body = escape(message.text).replace("\n", "<br>")
            parts.append(f"<p><b>{escape(message_label(message))}</b><br>{body}</p>")
        return "".join(parts)

    def _render_inbox(self, entries: tuple[InboxEntry, ...]) -> None:
        self.inbox_box.setVisible(bool(entries))
        shown = [self.inbox_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.inbox_list.count())]
        if shown == list(entries):
            return
        selected = self.inbox_list.currentItem()
        keep = selected.data(Qt.ItemDataRole.UserRole) if selected is not None else None
        self.inbox_list.clear()
        for entry in entries:
            item = QListWidgetItem(
                _("{title}, {time}").format(
                    title=entry.title or _("(untitled)"), time=time.strftime("%H:%M", time.localtime(entry.ready_at))
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.inbox_list.addItem(item)
            if entry == keep:
                self.inbox_list.setCurrentItem(item)
        if self.inbox_list.currentItem() is None:
            self.inbox_text.hide()

    def _render_pending(self, entries: tuple[PendingEntry, ...]) -> None:
        self.pending_box.setVisible(bool(entries))
        shown = [self.pending_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.pending_list.count())]
        if shown == list(entries):
            return
        self.pending_list.clear()
        for entry in entries:
            since = time.strftime("%H:%M", time.localtime(entry.since))
            title = entry.title or _("(untitled)")
            text = (
                _("{title}: {reason}, since {time}").format(title=title, reason=entry.reason, time=since)
                if entry.reason
                else _("{title}, since {time}").format(title=title, time=since)
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.pending_list.addItem(item)

    def _show_answer(self, item: QListWidgetItem | None) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(entry, InboxEntry):
            self.inbox_text.hide()
            return
        self.inbox_text.setPlainText(entry.answer or _("(no text in the answer)"))
        self.inbox_text.show()

    @property
    def snapshot(self) -> AssistantSnapshot:
        return self._snapshot

    # ------------------------------------------------------------------ actions

    def _submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or not self.input.isEnabled():
            return
        self.input.clear()
        self._service.send(text)

    def present(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        if self.input.isEnabled():
            self.input.setFocus()

    def set_quitting(self) -> None:
        self._quitting = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self.hide()
