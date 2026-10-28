"""The window that installs a transcription mode (`make stt-install`) and shows its output.

Opened only after the user agreed (see `CompanionUi.ensure_stt_ready`). The install runs on
a worker thread through the controller; each output line hops to the GUI thread. It cannot
be cancelled half way (pip would leave a broken environment), so the window only closes
once it is over; VoiceMate keeps working meanwhile.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from PySide6.QtGui import QCloseEvent, QFont, QFontDatabase
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QProgressBar, QVBoxLayout, QWidget

from app.companion.contract import CompanionSettings
from app.companion.ui.icons import app_icon
from app.companion.ui.shell import Shell
from app.i18n import _
from app.protocol.models import SttDevice

MAX_LOG_LINES: Final = 2000


def mode_title(mode: SttDevice) -> str:
    return _("GPU mode") if mode == "gpu" else _("CPU mode")


class SttInstallDialog(QDialog):
    def __init__(
        self,
        shell: Shell,
        settings: CompanionSettings,
        mode: SttDevice,
        on_done: Callable[[bool], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shell = shell
        self._settings = settings
        self._mode = mode
        self._on_done = on_done
        self._running = False
        self.setWindowTitle(_("Installing the {mode}").format(mode=mode_title(mode)))
        self.setWindowIcon(app_icon())
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        self.message = QLabel(
            _("Downloading and installing. This can take several minutes; VoiceMate keeps working meanwhile.")
        )
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # busy: pip gives no overall percentage
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(MAX_LOG_LINES)
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setFont(QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)))
        self.output.setAccessibleName(_("Installation output"))
        layout.addWidget(self.output, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.close_button = self.buttons.button(QDialogButtonBox.StandardButton.Close)
        self.close_button.setText(_("Close"))
        self.buttons.rejected.connect(self.close)
        layout.addWidget(self.buttons)

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        self._running = True
        self.close_button.setEnabled(False)
        controller, settings, mode, bridge = self._shell.controller, self._settings, self._mode, self._shell.bridge

        def line(text: str) -> None:
            bridge.call_soon(lambda: self.output.appendPlainText(text))

        bridge.run_async(lambda: controller.install_stt(settings, mode, line), self._finished, self._failed)
        self.show()
        self.raise_()
        self.activateWindow()

    def _failed(self, exc: BaseException) -> None:
        self.output.appendPlainText(str(exc))
        self._finished(False)

    def _finished(self, ok: bool) -> None:
        self._running = False
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if ok else 0)
        self.close_button.setEnabled(True)
        if ok:
            self.message.setText(_("The {mode} is installed. Switching to it now.").format(mode=mode_title(self._mode)))
        else:
            self.message.setText(
                _("The installation failed. The details are below; the engine keeps the current mode.")
            )
        self._on_done(ok)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._running and not self._shell.quitting:
            event.ignore()  # stopping pip half way would leave the engine's environment broken
            return
        super().closeEvent(event)

    def reject(self) -> None:
        # Esc goes through reject(), not closeEvent(): same rule.
        if self._running and not self._shell.quitting:
            return
        super().reject()
