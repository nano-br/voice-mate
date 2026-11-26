"""The window that explains how to sign in to Claude again when the assistant's login expired.

Written for someone who does not know that VoiceMate runs the Claude Code program: it says in plain
words what happened, the steps to renew the sign-in, which `claude` program VoiceMate uses (the one
to sign in with), a button that copies the command and a link to the guide in the interface's
language. It opens from the tray (click, menu or notification) and from the assistant window.
"""

from __future__ import annotations

import os
from html import escape
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.companion.assistant.login import LOGIN_COMMAND, guide_url
from app.companion.ui.icons import app_icon, state_pixmap
from app.i18n import _, active_language

HEADER_ICON_SIZE = 40  # as in the status window
WINDOW_WIDTH = 540


def display_path(path: str) -> str:
    """`path` with the home folder written as %USERPROFILE%: the person's account name stays
    off the screen (a screen share, a screenshot sent for help) and the path still works when
    pasted in a terminal or in Explorer."""
    home = (os.environ.get("USERPROFILE") or str(Path.home())).rstrip("\\/")
    if home and os.path.normcase(path).startswith(os.path.normcase(home) + os.sep):
        return "%USERPROFILE%" + path[len(home) :]
    return path


class LoginHelpDialog(QDialog):
    """`cli` is the `claude` program the assistant runs (`own_session.find_cli()`), None when none was found."""

    def __init__(self, cli: str | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cli = cli
        self.setWindowTitle(_("Sign in to Claude again"))
        self.setWindowIcon(app_icon())
        self.guide_url = ""

        layout = QVBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # The header of the status window: the mark with the attention badge and a bigger title.
        header = QHBoxLayout()
        header.setSpacing(12)
        self.icon = QLabel()
        self.icon.setFixedSize(HEADER_ICON_SIZE, HEADER_ICON_SIZE)
        header.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        self.title = QLabel(_("Sign in to Claude again"))
        font = QFont(self.title.font())
        font.setPointSizeF(font.pointSizeF() * 1.3)
        font.setWeight(QFont.Weight.DemiBold)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        header.addWidget(self.title, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)
        self._render_icon()

        self.intro = QLabel(
            _(
                "The assistant needs you to sign in to Claude again. Its sign-in expired, so it cannot "
                "answer until you renew it. This takes about a minute and nothing you said or wrote is lost."
            )
        )
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)

        self.steps = QLabel(
            "<ol><li>{one}</li><li>{two}</li><li>{three}</li></ol>".format(
                one=escape(_("Open a terminal (Windows Terminal, PowerShell or Command Prompt).")),
                two=escape(_("Type the command below and press Enter.")),
                three=escape(
                    _("Follow the page that opens in your browser, then come back and talk to the assistant.")
                ),
            )
        )
        self.steps.setWordWrap(True)
        layout.addWidget(self.steps)

        # The command in a tinted box (the banner style of the status window), in the window's
        # own font: only bold sets it apart.
        command_box = QFrame()
        command_box.setObjectName("command")
        command_box.setStyleSheet(
            "QFrame#command { background-color: rgba(59, 130, 246, 0.16);"
            " border: 1px solid rgba(59, 130, 246, 0.6); border-radius: 4px; }"
        )
        command_row = QHBoxLayout(command_box)
        command_row.setContentsMargins(10, 8, 10, 8)
        self.command_label = QLabel(f"<b>{escape(LOGIN_COMMAND)}</b>")
        self.command_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        command_row.addWidget(self.command_label, 1)
        self.copy_button = QPushButton(_("Copy the command"))
        self.copy_button.clicked.connect(self.copy_command)
        command_row.addWidget(self.copy_button)
        layout.addWidget(command_box)

        self.alternative = QLabel(
            _('Older versions of Claude Code have no such command: type "claude", press Enter, then type "/login".')
        )
        self.alternative.setWordWrap(True)
        layout.addWidget(self.alternative)

        self.program = QLabel()
        self.program.setWordWrap(True)
        self.program.setTextFormat(Qt.TextFormat.RichText)
        self.program.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.program)

        self.guide = QLabel()
        self.guide.setOpenExternalLinks(True)
        self.guide.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        layout.addWidget(self.guide)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.close_button = QPushButton(_("Close"))
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.refresh(cli)

    def refresh(self, cli: str | None) -> None:
        """The program and the guide's language can change while the window is kept: read them again."""
        self._cli = cli
        self.guide_url = guide_url(active_language())
        self.program.setText(self.program_text())
        link = '<a href="{url}">{text}</a>'.format(url=escape(self.guide_url), text=escape(_("Open the guide")))
        self.guide.setText(link)
        self._fit()

    def _fit(self) -> None:
        """Tall enough for every wrapped text at the window's width: their length depends on the
        language and on the program path, and a top-level window does not grow by itself."""
        layout = self._layout
        layout.activate()
        width = max(self.width(), WINDOW_WIDTH) if self.isVisible() else WINDOW_WIDTH
        height = layout.totalHeightForWidth(WINDOW_WIDTH)
        self.setMinimumSize(WINDOW_WIDTH, height)
        self.resize(width, max(self.height(), layout.totalHeightForWidth(width)) if self.isVisible() else height)

    def program_text(self) -> str:
        """Which `claude` program VoiceMate runs: sign in with that one."""
        if self._cli is None:
            return escape(
                _(
                    "VoiceMate did not find the Claude Code program on this computer. Install it first, "
                    "or set VOICEMATE_CLAUDE_CLI to its path, then restart the assistant."
                )
            )
        return "{text}<br><b>{path}</b>".format(
            text=escape(_("VoiceMate uses this Claude Code program, so sign in with it:")),
            path=escape(display_path(self._cli)),
        )

    def _render_icon(self) -> None:
        glyph = self.palette().color(QPalette.ColorRole.WindowText)
        pixmap = state_pixmap("ready", glyph, HEADER_ICON_SIZE, self.devicePixelRatioF(), attention=True)
        self.icon.setPixmap(pixmap)

    def changeEvent(self, event: QEvent) -> None:
        # The mark follows the window text color when Windows switches between light and dark.
        if event.type() == QEvent.Type.PaletteChange:
            self._render_icon()
        super().changeEvent(event)

    def copy_command(self) -> None:
        QGuiApplication.clipboard().setText(LOGIN_COMMAND)

    def present(self, cli: str | None) -> None:
        self.refresh(cli)
        self.show()
        self.raise_()
        self.activateWindow()
