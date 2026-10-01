"""The settings window: Hotkeys, Sounds and General pages, applied with `apply_settings`.

The dialog edits a copy of `controller.settings()` and sends the whole
`CompanionSettings` on OK/Apply (on a worker thread: applying re-registers the
hotkeys). Errors come back localized from the controller and stay visible at the
bottom of the window until the next apply.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.companion.contract import (
    CUE_NAMES,
    SETTINGS_VERSION,
    CompanionSettings,
    CompanionSnapshot,
    CueName,
    CuePreset,
    CueSettings,
    CueSource,
    EngineMode,
    FlowEntry,
    HotkeyBinding,
    HotkeyCheck,
    NotifyLevel,
    UiLanguage,
    WslRestartPolicy,
)
from app.companion.ui import texts
from app.companion.ui.hotkey_edit import HotkeyEdit
from app.companion.ui.icons import app_icon
from app.companion.ui.shell import Shell
from app.companion.ui.view_model import chord_for, displayed_flows
from app.i18n import _

SAVED_MESSAGE_MS = 4000


def _option_combo[T: str](labels: dict[T, str], allowed: Sequence[T] | None = None) -> QComboBox:
    combo = QComboBox()
    for value, label in labels.items():
        if allowed is None or value in allowed:
            combo.addItem(label, value)
    return combo


def _select[T: str](combo: QComboBox, value: T, labels: dict[T, str]) -> None:
    index = combo.findData(value)
    if index < 0 and value in labels:  # a value hidden on this platform, but saved: keep it visible
        combo.addItem(labels[value], value)
        index = combo.count() - 1
    combo.setCurrentIndex(max(index, 0))


def _status_color(widget: QWidget, ok: bool) -> str:
    """Green/orange that stay readable on the current palette (light or dark window)."""
    dark = widget.palette().color(QPalette.ColorRole.Window).lightness() < 128
    if ok:
        return "#4CC38A" if dark else "#1E7F46"
    return "#FF8A5B" if dark else "#C2410C"


def _percent_slider(accessible_name: str) -> QSlider:
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setSingleStep(5)
    slider.setPageStep(10)
    slider.setAccessibleName(accessible_name)
    return slider


def _secondary(label: QLabel) -> QLabel:
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    label.setWordWrap(True)
    return label


# ---------------------------------------------------------------------- Hotkeys page


@dataclass
class _HotkeyRow:
    flow: FlowEntry
    edit: HotkeyEdit
    status: QLabel
    token: object = field(default_factory=object)


class HotkeysPage(QWidget):
    changed = Signal()

    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._rows: list[_HotkeyRow] = []
        self._engine_owned = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.intro = _secondary(QLabel(_("Click a shortcut, then press the new keys. Esc cancels, Backspace clears.")))
        layout.addWidget(self.intro)
        self.engine_note = _secondary(
            QLabel(_("The engine owns these shortcuts. Change them in the engine configuration."))
        )
        layout.addWidget(self.engine_note)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(2, 1)
        layout.addLayout(self.grid)
        self.rule = _secondary(QLabel(texts.hotkey_rule()))
        layout.addWidget(self.rule)
        layout.addStretch(1)
        defaults_row = QHBoxLayout()
        defaults_row.addStretch(1)
        self.defaults_button = QPushButton(_("Restore defaults"))
        self.defaults_button.setAutoDefault(False)  # Enter must press OK, never reset the shortcuts
        self.defaults_button.clicked.connect(self.restore_defaults)
        defaults_row.addWidget(self.defaults_button)
        layout.addLayout(defaults_row)

    @property
    def rows(self) -> list[_HotkeyRow]:
        return list(self._rows)

    def load(self, settings: CompanionSettings, snapshot: CompanionSnapshot, read_only: bool) -> None:
        self.cancel_capture()
        while self.grid.count():
            item = self.grid.takeAt(0)
            old = item.widget() if item is not None else None
            if old is not None:
                old.deleteLater()
        self._rows = []
        self._engine_owned = snapshot.hotkeys_owned_by_engine
        self.engine_note.setVisible(self._engine_owned)
        self.intro.setVisible(not self._engine_owned)
        self.rule.setVisible(not self._engine_owned)
        self.defaults_button.setVisible(not self._engine_owned)
        self.defaults_button.setEnabled(not read_only)
        flows = displayed_flows(snapshot, settings)
        for index, flow in enumerate(flows):
            title = texts.flow_title(flow, flows)
            label = QLabel(title + ":")
            edit = HotkeyEdit(chord_for(flow, snapshot, settings))
            edit.setAccessibleName(_("Shortcut for {action}").format(action=title))
            edit.set_editable(not self._engine_owned and not read_only)
            label.setBuddy(edit)
            status = QLabel()
            status.setTextFormat(Qt.TextFormat.PlainText)
            row = _HotkeyRow(flow, edit, status)
            edit.chord_changed.connect(lambda _chord, current=row: self._on_chord_changed(current))
            edit.capture_started.connect(lambda: self._shell.controller.suspend_hotkeys(True))
            edit.capture_finished.connect(lambda: self._shell.controller.suspend_hotkeys(False))
            self.grid.addWidget(label, index, 0)
            self.grid.addWidget(edit, index, 1)
            self.grid.addWidget(status, index, 2)
            self._rows.append(row)
        # Tab order: the shortcut fields first (they were created after the button).
        previous: QWidget | None = None
        for row in self._rows:
            if previous is not None:
                QWidget.setTabOrder(previous, row.edit)
            previous = row.edit
        if previous is not None:
            QWidget.setTabOrder(previous, self.defaults_button)
        for row in self._rows:
            if self._engine_owned:
                self._show_status(row, "engine_owned")
            else:
                self._validate(row)

    def chords(self) -> dict[str, str]:
        return {row.flow.name: row.edit.chord for row in self._rows}

    def collect(self, base: tuple[HotkeyBinding, ...]) -> tuple[HotkeyBinding, ...]:
        """The edited bindings; flows not shown (not served by the engine) are kept as they are."""
        if self._engine_owned:
            return base
        edited = self.chords()
        bindings = [HotkeyBinding(b.flow, edited.pop(b.flow)) if b.flow in edited else b for b in base]
        bindings.extend(HotkeyBinding(flow, chord) for flow, chord in edited.items())
        return tuple(bindings)

    def restore_defaults(self) -> None:
        defaults = {binding.flow: binding.chord for binding in CompanionSettings().hotkeys}
        for row in self._rows:
            chord = defaults.get(row.flow.name, defaults.get(row.flow.kind, ""))
            if chord != row.edit.chord:
                row.edit.set_chord(chord)
        for row in self._rows:
            self._validate(row)
        self.changed.emit()

    def cancel_capture(self) -> None:
        for row in self._rows:
            row.edit.finish_capture(None)

    def _on_chord_changed(self, row: _HotkeyRow) -> None:
        # A change can create or resolve a duplicate in the other rows too.
        for current in self._rows:
            self._validate(current)
        self.changed.emit()

    def _validate(self, row: _HotkeyRow) -> None:
        chord = row.edit.chord
        row.token = token = object()
        if not chord:
            self._show_neutral(row, _("No shortcut: use the menu"))
            return
        if any(other is not row and other.edit.chord == chord for other in self._rows):
            self._show_status(row, "duplicate")
            return
        self._show_neutral(row, _("Checking..."))

        def current() -> bool:
            # Still the latest check of a row that still exists (load() rebuilds the rows).
            return row.token is token and any(existing is row for existing in self._rows)

        def done(result: HotkeyCheck) -> None:
            if current():
                # The controller compares with the SAVED bindings; an edit in this window may
                # already have moved the other flow's chord away.
                if result == "duplicate" and chord not in self._other_chords(row):
                    result = "ok"
                self._show_status(row, result)

        def failed(_exc: BaseException) -> None:
            if current():
                self._show_neutral(row, "")

        self._shell.bridge.run_async(lambda: self._shell.controller.check_hotkey(chord, row.flow.name), done, failed)

    def _other_chords(self, row: _HotkeyRow) -> set[str]:
        return {other.edit.chord for other in self._rows if other is not row}

    def _show_status(self, row: _HotkeyRow, result: HotkeyCheck) -> None:
        text = texts.hotkey_check_labels()[result]
        color = _status_color(self, result == "ok")
        if result == "engine_owned":
            self._show_neutral(row, text)
            return
        row.status.setText(text)
        row.status.setStyleSheet(f"color: {color};")
        row.status.setToolTip(texts.hotkey_rule() if result == "invalid" else "")
        row.status.setAccessibleName(text)

    def _show_neutral(self, row: _HotkeyRow, text: str) -> None:
        row.status.setText(text)
        row.status.setStyleSheet("")
        row.status.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        row.status.setToolTip("")
        row.status.setAccessibleName(text)


# ---------------------------------------------------------------------- Sounds page


@dataclass
class _CueRow:
    cue: CueName
    enabled: QCheckBox
    source: QComboBox
    preset: QComboBox
    file: QLineEdit
    browse: QToolButton
    stack: QStackedWidget
    volume: QSlider
    volume_label: QLabel
    preview: QToolButton


class SoundsPage(QWidget):
    changed = Signal()

    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        master = QHBoxLayout()
        master.setSpacing(10)
        self.cues_enabled = QCheckBox(_("Play sound cues"))
        self.cues_enabled.toggled.connect(self._on_master_toggled)
        master.addWidget(self.cues_enabled)
        master.addStretch(1)
        volume_label = QLabel(_("Volume:"))
        self.master_volume = _percent_slider(_("Master volume"))
        self.master_volume.setMinimumWidth(140)
        volume_label.setBuddy(self.master_volume)
        self.master_volume_label = QLabel()
        self.master_volume_label.setMinimumWidth(self.fontMetrics().horizontalAdvance("100%") + 4)
        self.master_volume.valueChanged.connect(lambda value: self.master_volume_label.setText(f"{value}%"))
        self.master_volume.valueChanged.connect(self.changed)
        master.addWidget(volume_label)
        master.addWidget(self.master_volume)
        master.addWidget(self.master_volume_label)
        layout.addLayout(master)

        self.cue_box = QGroupBox(_("Sounds"))
        grid = QGridLayout(self.cue_box)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(2, 1)
        self.rows: dict[CueName, _CueRow] = {}
        labels = texts.cue_labels()
        for index, cue in enumerate(CUE_NAMES):
            row = self._build_row(cue, labels[cue])
            grid.addWidget(row.enabled, index, 0)
            grid.addWidget(row.source, index, 1)
            grid.addWidget(row.stack, index, 2)
            grid.addWidget(row.volume, index, 3)
            grid.addWidget(row.volume_label, index, 4)
            grid.addWidget(row.preview, index, 5)
            self.rows[cue] = row
        layout.addWidget(self.cue_box)
        layout.addWidget(_secondary(QLabel(_("Custom sounds must be WAV files (PCM, 8 or 16 bit)."))))
        layout.addStretch(1)

    def _build_row(self, cue: CueName, label: str) -> _CueRow:
        enabled = QCheckBox(label)
        enabled.setAccessibleName(label)
        source = _option_combo(texts.cue_source_labels())
        source.setAccessibleName(_("Source: {sound}").format(sound=label))
        preset = _option_combo(texts.cue_preset_labels())
        preset.setAccessibleName(_("Built-in sound: {sound}").format(sound=label))
        file_edit = QLineEdit()
        file_edit.setReadOnly(True)
        file_edit.setPlaceholderText(_("No file chosen"))
        file_edit.setAccessibleName(_("Sound file: {sound}").format(sound=label))
        browse = QToolButton()
        browse.setText(_("Browse..."))
        browse.setAccessibleName(_("Choose a file for {sound}").format(sound=label))
        file_box = QWidget()
        file_layout = QHBoxLayout(file_box)
        file_layout.setContentsMargins(0, 0, 0, 0)
        file_layout.setSpacing(4)
        file_layout.addWidget(file_edit, 1)
        file_layout.addWidget(browse)
        stack = QStackedWidget()
        stack.addWidget(preset)
        stack.addWidget(file_box)
        stack.setMinimumWidth(190)
        stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        volume = _percent_slider(_("Volume: {sound}").format(sound=label))
        volume.setMinimumWidth(90)
        volume_label = QLabel()
        volume_label.setMinimumWidth(self.fontMetrics().horizontalAdvance("100%") + 4)
        preview = QToolButton()
        preview.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        preview.setToolTip(_("Preview"))
        preview.setAccessibleName(_("Preview: {sound}").format(sound=label))
        row = _CueRow(cue, enabled, source, preset, file_edit, browse, stack, volume, volume_label, preview)

        enabled.toggled.connect(lambda _on: self._sync_row(row))
        source.currentIndexChanged.connect(lambda _index: self._sync_row(row))
        for signal in (enabled.toggled, source.currentIndexChanged, preset.currentIndexChanged, volume.valueChanged):
            signal.connect(self.changed)
        volume.valueChanged.connect(lambda value: volume_label.setText(f"{value}%"))
        browse.clicked.connect(lambda: self._choose_file(row))
        preview.clicked.connect(lambda: self._preview(row))
        return row

    def load(self, settings: CompanionSettings) -> None:
        with QSignalBlocker(self):
            self.cues_enabled.setChecked(settings.cues_enabled)
            self.master_volume.setValue(round(settings.master_volume * 100))
            self.master_volume_label.setText(f"{self.master_volume.value()}%")
            for cue, row in self.rows.items():
                cue_settings = settings.cues.get(cue, CueSettings())
                row.enabled.setChecked(cue_settings.enabled)
                row.source.setCurrentIndex(max(row.source.findData(cue_settings.source), 0))
                row.preset.setCurrentIndex(max(row.preset.findData(cue_settings.preset), 0))
                row.file.setText(cue_settings.file)
                row.file.setToolTip(cue_settings.file)
                row.volume.setValue(round(cue_settings.volume * 100))
                row.volume_label.setText(f"{row.volume.value()}%")
                self._sync_row(row)
        self._on_master_toggled(settings.cues_enabled)

    def cue_settings(self, row: _CueRow) -> CueSettings:
        source: CueSource = row.source.currentData()
        preset: CuePreset = row.preset.currentData()
        return CueSettings(
            enabled=row.enabled.isChecked(),
            source=source,
            preset=preset,
            file=row.file.text(),
            volume=row.volume.value() / 100,
        )

    def collect(self) -> tuple[bool, float, dict[CueName, CueSettings]]:
        cues = {cue: self.cue_settings(row) for cue, row in self.rows.items()}
        return self.cues_enabled.isChecked(), self.master_volume.value() / 100, cues

    def _sync_row(self, row: _CueRow) -> None:
        is_file = row.source.currentData() == "file"
        row.stack.setCurrentIndex(1 if is_file else 0)
        on = row.enabled.isChecked()
        for widget in (row.source, row.stack, row.volume, row.volume_label):
            widget.setEnabled(on)

    def _on_master_toggled(self, enabled: bool) -> None:
        self.cue_box.setEnabled(enabled)
        self.changed.emit()

    def _choose_file(self, row: _CueRow) -> None:
        start = row.file.text() or str(Path.home())
        path, _filter = QFileDialog.getOpenFileName(self, _("Choose a sound file"), start, _("WAV audio") + " (*.wav)")
        if path:
            row.file.setText(str(Path(path)))
            row.file.setToolTip(str(Path(path)))
            self.changed.emit()

    def _preview(self, row: _CueRow) -> None:
        settings = self.cue_settings(row)
        master = self.master_volume.value() / 100
        controller = self._shell.controller
        self._shell.bridge.run_async(lambda: controller.preview_cue(row.cue, settings, master), lambda _r: None)


# ---------------------------------------------------------------------- General page


class GeneralPage(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        app_form = QFormLayout()
        app_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.language = _option_combo(texts.language_labels())
        app_form.addRow(_("Language:"), self.language)
        language_note = _secondary(QLabel(_("Applies the next time VoiceMate starts.")))
        language_note.setWordWrap(False)  # one short line; wrapping in a form row misplaces it
        app_form.addRow("", language_note)
        self.notify_level = _option_combo(texts.notify_level_labels())
        app_form.addRow(_("Notifications:"), self.notify_level)
        self.start_at_login = QCheckBox(_("Start VoiceMate when I sign in"))
        app_form.addRow("", self.start_at_login)
        layout.addLayout(app_form)

        engine_box = QGroupBox(_("Engine"))
        engine_form = QFormLayout(engine_box)
        engine_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        allowed_modes: tuple[EngineMode, ...] = (
            ("wsl2", "external") if sys.platform == "win32" else ("local", "external")
        )
        self.engine_mode = _option_combo(texts.engine_mode_labels(), allowed_modes)
        engine_form.addRow(_("Mode:"), self.engine_mode)
        self.wsl_distro = QLineEdit()
        self.wsl_distro.setPlaceholderText(_("Default distro"))
        engine_form.addRow(_("WSL distro:"), self.wsl_distro)
        self.engine_dir = QLineEdit()
        self.engine_dir.setPlaceholderText(_("Detect automatically"))
        self.engine_dir.setToolTip(_("The voice-mate folder: relative to your home folder, or absolute."))
        engine_form.addRow(_("Engine folder:"), self.engine_dir)
        self.wsl_restart_policy = _option_combo(texts.wsl_restart_labels())
        self.wsl_restart_policy.setToolTip(
            _("Restarting WSL brings its audio back, but it stops every running distro, Docker included.")
        )
        engine_form.addRow(_("Restart WSL when audio fails:"), self.wsl_restart_policy)
        self._wsl_rows = (self.wsl_distro, self.wsl_restart_policy)
        layout.addWidget(engine_box)
        layout.addStretch(1)

        for combo in (self.language, self.notify_level, self.engine_mode, self.wsl_restart_policy):
            combo.currentIndexChanged.connect(self.changed)
        self.engine_mode.currentIndexChanged.connect(lambda _index: self._sync_mode())
        self.start_at_login.toggled.connect(self.changed)
        self.wsl_distro.textChanged.connect(self.changed)
        self.engine_dir.textChanged.connect(self.changed)
        for widget, name in (
            (self.language, _("Language")),
            (self.notify_level, _("Notifications")),
            (self.engine_mode, _("Mode")),
            (self.wsl_distro, _("WSL distro")),
            (self.engine_dir, _("Engine folder")),
            (self.wsl_restart_policy, _("Restart WSL when audio fails")),
        ):
            widget.setAccessibleName(name)

    def load(self, settings: CompanionSettings) -> None:
        with QSignalBlocker(self):
            _select(self.language, settings.language, texts.language_labels())
            _select(self.notify_level, settings.notify_level, texts.notify_level_labels())
            self.start_at_login.setChecked(settings.start_at_login)
            _select(self.engine_mode, settings.engine_mode, texts.engine_mode_labels())
            self.wsl_distro.setText(settings.wsl_distro)
            self.engine_dir.setText(settings.engine_dir)
            _select(self.wsl_restart_policy, settings.wsl_restart_policy, texts.wsl_restart_labels())
        self._sync_mode()

    def apply_to(self, settings: CompanionSettings) -> CompanionSettings:
        language: UiLanguage = self.language.currentData()
        notify_level: NotifyLevel = self.notify_level.currentData()
        engine_mode: EngineMode = self.engine_mode.currentData()
        policy: WslRestartPolicy = self.wsl_restart_policy.currentData()
        return replace(
            settings,
            language=language,
            notify_level=notify_level,
            start_at_login=self.start_at_login.isChecked(),
            engine_mode=engine_mode,
            wsl_distro=self.wsl_distro.text().strip(),
            engine_dir=self.engine_dir.text().strip(),
            wsl_restart_policy=policy,
        )

    def _sync_mode(self) -> None:
        mode = self.engine_mode.currentData()
        for widget in self._wsl_rows:
            widget.setEnabled(mode == "wsl2")
        self.engine_dir.setEnabled(mode != "external")


# ---------------------------------------------------------------------- dialog


class SettingsDialog(QDialog):
    applied = Signal(object)  # CompanionSettings, after a successful apply

    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._baseline = shell.settings()
        self._busy = False
        self._read_only = False
        self.setWindowTitle(_("VoiceMate Settings"))
        self.setWindowIcon(app_icon())
        self.setMinimumSize(620, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.read_only_note = QLabel(
            _("These settings were saved by a newer version of VoiceMate. They are shown read-only.")
        )
        self.read_only_note.setWordWrap(True)
        self.read_only_note.setStyleSheet(f"color: {_status_color(self, False)};")
        layout.addWidget(self.read_only_note)
        self.tabs = QTabWidget()
        self.hotkeys_page = HotkeysPage(shell)
        self.sounds_page = SoundsPage(shell)
        self.general_page = GeneralPage()
        self.tabs.addTab(self.hotkeys_page, _("Hotkeys"))
        self.tabs.addTab(self.sounds_page, _("Sounds"))
        self.tabs.addTab(self.general_page, _("General"))
        layout.addWidget(self.tabs, 1)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.setAccessibleName(_("Messages"))
        self.message.hide()
        layout.addWidget(self.message)

        # Our own button texts: Qt's standard buttons would come from Qt's catalogs.
        self.buttons = QDialogButtonBox()
        self.ok_button = self.buttons.addButton(_("OK"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_button = self.buttons.addButton(_("Apply"), QDialogButtonBox.ButtonRole.ApplyRole)
        self.ok_button.setDefault(True)
        self.buttons.clicked.connect(self._on_button)
        layout.addWidget(self.buttons)

        for page in (self.hotkeys_page, self.sounds_page, self.general_page):
            page.changed.connect(self._update_buttons)
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self.message.hide)
        self.load()

    # ------------------------------------------------------------------ state

    def load(self) -> None:
        """(Re)read the applied settings and the engine's flows into every page."""
        self._baseline = self._shell.controller.settings()
        self._read_only = self._baseline.version > SETTINGS_VERSION
        self.read_only_note.setVisible(self._read_only)
        snapshot = self._shell.bridge.snapshot
        self.hotkeys_page.load(self._baseline, snapshot, self._read_only)
        self.sounds_page.load(self._baseline)
        self.general_page.load(self._baseline)
        for page in (self.sounds_page, self.general_page):
            page.setEnabled(not self._read_only)
        self.message.hide()
        self._update_buttons()

    def collect(self) -> CompanionSettings:
        cues_enabled, master_volume, cues = self.sounds_page.collect()
        edited = replace(
            self._baseline,
            hotkeys=self.hotkeys_page.collect(self._baseline.hotkeys),
            cues_enabled=cues_enabled,
            master_volume=master_volume,
            cues=cues,
        )
        return self.general_page.apply_to(edited)

    def is_dirty(self) -> bool:
        return self.collect() != self._baseline

    def _update_buttons(self) -> None:
        dirty = not self._read_only and self.is_dirty()
        self.apply_button.setEnabled(dirty and not self._busy)
        self.ok_button.setEnabled(not self._busy and not self._read_only)

    # ------------------------------------------------------------------ apply

    def _on_button(self, button: QAbstractButton) -> None:
        if button is self.ok_button:
            self.apply(close_after=True)
        elif button is self.apply_button:
            self.apply(close_after=False)
        else:
            self.reject()

    def apply(self, close_after: bool) -> None:
        if self._busy:
            return
        self.hotkeys_page.cancel_capture()
        settings = self.collect()
        if settings == self._baseline:
            if close_after:
                self.accept()
            return
        self._busy = True
        self._update_buttons()
        self._show_message(_("Saving..."), None)
        controller = self._shell.controller
        self._shell.bridge.run_async(
            lambda: controller.apply_settings(settings),
            lambda errors: self._applied(settings, errors, close_after),
            lambda exc: self._applied(settings, [_("Unexpected error: {error}").format(error=exc)], False),
        )

    def _applied(self, settings: CompanionSettings, errors: list[str], close_after: bool) -> None:
        self._busy = False
        if errors:
            items = "".join(f"<li>{_escape(error)}</li>" for error in errors)
            header = _escape(_("The settings were not saved:"))
            self._show_message(f"{header}<ul style='margin: 2px 0 0 -20px;'>{items}</ul>", _status_color(self, False))
            self._update_buttons()
            return
        self._baseline = settings
        self._shell.settings_applied(settings)
        self.applied.emit(settings)
        self._update_buttons()
        if close_after:
            self.message.hide()
            self.accept()
            return
        self._show_message(_escape(_("Settings saved.")), _status_color(self, True))
        self._message_timer.start(SAVED_MESSAGE_MS)

    def _show_message(self, html: str, color: str | None) -> None:
        self._message_timer.stop()
        self.message.setStyleSheet(f"color: {color};" if color else "")
        self.message.setText(html)
        self.message.show()

    @property
    def error_text(self) -> str:
        return self.message.text() if self.message.isVisible() else ""

    # ------------------------------------------------------------------ closing

    def done(self, result: int) -> None:
        self.hotkeys_page.cancel_capture()  # never leave the global hotkeys suspended
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hotkeys_page.cancel_capture()
        super().closeEvent(event)

    def present(self, reload: bool) -> None:
        if reload and not self._busy:
            self.load()
        self.show()
        self.raise_()
        self.activateWindow()
        self.tabs.tabBar().setFocus(Qt.FocusReason.ActiveWindowFocusReason)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
