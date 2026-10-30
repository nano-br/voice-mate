"""The settings window: Hotkeys, Sounds, Assistant and General pages, applied with `apply_settings`.

The dialog edits a copy of `controller.settings()` and sends the whole
`CompanionSettings` on OK/Apply (on a worker thread: applying re-registers the
hotkeys). Errors come back localized from the controller and stay visible at the
bottom of the window until the next apply.

The UI language is picked once at startup: when an apply changes it to one that selects
another catalog, the app asks whether to restart now, and the Language row shows a hint
while the selected language differs from the running one.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.companion.assistant import personality
from app.companion.assistant.contract import AssistantService, ProfileStatus
from app.companion.assistant.session_reader import list_conversations
from app.companion.contract import (
    ASSISTANT_HOTKEY_ACTIONS,
    CUE_NAMES,
    AnswerDelivery,
    AssistantEffort,
    AssistantHotkeyAction,
    AssistantMode,
    AssistantModel,
    AssistantPermission,
    AssistantSettings,
    AssistantVoiceSource,
    CompanionSettings,
    CompanionSnapshot,
    CueName,
    CuePreset,
    CueSettings,
    CueSource,
    DictationLanguage,
    EngineMode,
    ExpressionsMode,
    FlowEntry,
    HotkeyBinding,
    HotkeyCheck,
    NotifyLevel,
    PersonalitySource,
    SkillLanguage,
    TtsChoice,
    TtsProbe,
    UiLanguage,
    WslRestartPolicy,
)
from app.companion.paths import home_dir
from app.companion.stt import engine_stt
from app.companion.ui import texts
from app.companion.ui.hotkey_edit import HotkeyEdit
from app.companion.ui.icons import app_icon
from app.companion.ui.shell import Shell
from app.companion.ui.view_model import chord_for, displayed_flows, settings_read_only
from app.i18n import _
from app.protocol.models import SttMode, SttModel
from app.protocol.tts_provider import DEFAULT_URL, Voice

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


def _slider_volume(slider: QSlider, loaded: float) -> float:
    """The slider's volume; the loaded value itself while the slider still shows it, so a
    volume such as 0.333 does not count as edited just by opening the window."""
    return loaded if slider.value() == round(loaded * 100) else slider.value() / 100


def _secondary(label: QLabel) -> QLabel:
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    label.setWordWrap(True)
    return label


def _voice_combo(default_label: str, tooltip: str) -> QComboBox:
    """An editable voice picker. Providers can offer hundreds of voices, so typing filters the
    list by any part of a voice's name or id; a typed id that matches no entry is kept as is."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    combo.setMaxVisibleItems(15)
    completer = QCompleter(combo.model(), combo)
    completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    combo.setCompleter(completer)
    line = combo.lineEdit()
    if line is not None:
        line.setPlaceholderText(default_label)
    combo.setToolTip(tooltip)
    return combo


def _voice_label(voice: Voice) -> str:
    if voice.name == voice.id or voice.name.endswith(f"({voice.id})"):
        return voice.name
    return f"{voice.name} ({voice.id})"


def _fill_voices(combo: QComboBox, voices: Sequence[Voice], current: str, default_label: str) -> None:
    """`default_label` (the voice "") first, then the voice the provider marks as its default,
    then the others by name. `current` stays selected, even when the provider does not list it."""
    ordered = sorted(voices, key=lambda voice: (not voice.default, voice.name.casefold(), voice.id))
    with QSignalBlocker(combo):
        combo.clear()
        combo.addItem(default_label, "")
        known = {""}
        for voice in ordered:
            if voice.id in known:
                continue
            combo.addItem(_voice_label(voice), voice.id)
            known.add(voice.id)
            if voice.languages:
                combo.setItemData(combo.count() - 1, ", ".join(voice.languages), Qt.ItemDataRole.ToolTipRole)
        if current not in known:
            combo.addItem(current, current)
        combo.setCurrentIndex(combo.findData(current))


def _combo_voice_id(combo: QComboBox) -> str:
    """The id of the picked voice, or what was typed when it matches none of the entries."""
    index = combo.currentIndex()
    text = str(combo.currentText()).strip()
    if index >= 0 and text == combo.itemText(index):
        return str(combo.itemData(index) or "")
    found = combo.findText(text, Qt.MatchFlag.MatchExactly)
    if found >= 0:  # a name picked from the search popup
        return str(combo.itemData(found) or "")
    return text


# ---------------------------------------------------------------------- Hotkeys page


@dataclass
class _HotkeyRow:
    flow: FlowEntry | None  # None: one of the assistant's actions (`action`)
    edit: HotkeyEdit
    status: QLabel
    token: object = field(default_factory=object)
    result: HotkeyCheck | None = None  # the colored status shown (re-colored on palette changes)
    action: AssistantHotkeyAction | None = None

    @property
    def name(self) -> str:
        """The flow or the assistant action, as the conflict checks know it."""
        return self.flow.name if self.flow is not None else self.action or ""


class HotkeysPage(QWidget):
    changed = Signal()

    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._rows: list[_HotkeyRow] = []
        self._assistant_rows: list[_HotkeyRow] = []
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
        self.assistant_title = QLabel(_("Assistant"))
        font = self.assistant_title.font()
        font.setBold(True)
        self.assistant_title.setFont(font)
        layout.addWidget(self.assistant_title)
        self.assistant_grid = QGridLayout()
        self.assistant_grid.setHorizontalSpacing(12)
        self.assistant_grid.setVerticalSpacing(10)
        self.assistant_grid.setColumnStretch(2, 1)
        layout.addLayout(self.assistant_grid)
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
        """The engine flows' rows."""
        return list(self._rows)

    @property
    def assistant_rows(self) -> list[_HotkeyRow]:
        return list(self._assistant_rows)

    def _all_rows(self) -> list[_HotkeyRow]:
        return self._rows + self._assistant_rows

    def load(self, settings: CompanionSettings, snapshot: CompanionSnapshot, read_only: bool) -> None:
        self.cancel_capture()
        for grid in (self.grid, self.assistant_grid):
            while grid.count():
                item = grid.takeAt(0)
                old = item.widget() if item is not None else None
                if old is not None:
                    old.deleteLater()
        self._rows = []
        self._assistant_rows = []
        self._engine_owned = snapshot.hotkeys_owned_by_engine
        self.engine_note.setVisible(self._engine_owned)
        self.intro.setVisible(not self._engine_owned)
        self.rule.setVisible(not self._engine_owned)
        self.defaults_button.setVisible(not self._engine_owned)
        self.defaults_button.setEnabled(not read_only)
        flows = displayed_flows(snapshot, settings)
        for index, flow in enumerate(flows):
            title = texts.flow_title(flow, flows)
            row = self._add_row(self.grid, index, title, chord_for(flow, snapshot, settings), read_only)
            row.flow = flow
            self._rows.append(row)
        titles = texts.assistant_hotkey_titles()
        for index, action in enumerate(ASSISTANT_HOTKEY_ACTIONS):
            row = self._add_row(
                self.assistant_grid, index, titles[action], settings.assistant.hotkey(action), read_only
            )
            row.action = action
            self._assistant_rows.append(row)
        # In this mode no assistant shortcut is registered (the engine owns the keyboard and has
        # no assistant shortcuts): showing their rows would promise keys that never fire.
        self.assistant_title.setVisible(not self._engine_owned)
        for row in self._assistant_rows:
            for widget in self._row_widgets(self.assistant_grid, row):
                widget.setVisible(not self._engine_owned)
        # Tab order: the shortcut fields first (they were created after the button).
        previous: QWidget | None = None
        for row in self._all_rows():
            if previous is not None:
                QWidget.setTabOrder(previous, row.edit)
            previous = row.edit
        if previous is not None:
            QWidget.setTabOrder(previous, self.defaults_button)
        for row in self._all_rows():
            if self._engine_owned:
                self._show_status(row, "engine_owned")
            else:
                self._validate(row)

    def _add_row(self, grid: QGridLayout, index: int, title: str, chord: str, read_only: bool) -> _HotkeyRow:
        label = QLabel(_("{action}:").format(action=title))
        edit = HotkeyEdit(chord)
        edit.setAccessibleName(_("Shortcut for {action}").format(action=title))
        edit.set_editable(not self._engine_owned and not read_only)
        label.setBuddy(edit)
        status = QLabel()
        status.setTextFormat(Qt.TextFormat.PlainText)
        row = _HotkeyRow(None, edit, status)
        edit.chord_changed.connect(lambda _chord, current=row: self._on_chord_changed(current))
        edit.capture_started.connect(lambda: self._suspend_hotkeys(True))
        edit.capture_finished.connect(lambda: self._suspend_hotkeys(False))
        grid.addWidget(label, index, 0)
        grid.addWidget(edit, index, 1)
        grid.addWidget(status, index, 2)
        return row

    @staticmethod
    def _row_widgets(grid: QGridLayout, row: _HotkeyRow) -> list[QWidget]:
        """The label, the shortcut field and the status of one row."""
        widgets: list[QWidget] = [row.edit, row.status]
        for index in range(grid.count()):
            item = grid.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QLabel) and widget.buddy() is row.edit:
                widgets.append(widget)
        return widgets

    def chords(self) -> dict[str, str]:
        return {row.name: row.edit.chord for row in self._rows}

    def assistant_chords(self) -> dict[str, str]:
        """action -> chord ("" = none) of the assistant's rows."""
        return {row.name: row.edit.chord for row in self._assistant_rows}

    def collect(self, base: tuple[HotkeyBinding, ...]) -> tuple[HotkeyBinding, ...]:
        """The edited bindings. A cleared shortcut drops its binding (no binding = no hotkey;
        the core rejects empty chords); flows not shown here are kept as they are."""
        if self._engine_owned:
            return base
        edited = self.chords()
        bindings: list[HotkeyBinding] = []
        for binding in base:
            chord = edited.pop(binding.flow, binding.chord)
            if chord:
                bindings.append(HotkeyBinding(binding.flow, chord))
        bindings.extend(HotkeyBinding(flow, chord) for flow, chord in edited.items() if chord)
        return tuple(bindings)

    def _suspend_hotkeys(self, suspended: bool) -> None:
        # One serial worker: never block the GUI, and never resume before suspending.
        controller = self._shell.controller
        self._shell.bridge.run_serial(lambda: controller.suspend_hotkeys(suspended))

    def restore_defaults(self) -> None:
        defaults = {binding.flow: binding.chord for binding in CompanionSettings().hotkeys}
        assistant = AssistantSettings()
        for row in self._all_rows():
            if row.flow is not None:
                chord = defaults.get(row.flow.name, defaults.get(row.flow.kind, ""))
            else:
                chord = assistant.hotkey(row.action) if row.action is not None else ""
            if chord != row.edit.chord:
                row.edit.set_chord(chord)
        for row in self._all_rows():
            self._validate(row)
        self.changed.emit()

    def cancel_capture(self) -> None:
        for row in self._all_rows():
            row.edit.finish_capture(None)

    def _on_chord_changed(self, row: _HotkeyRow) -> None:
        # A change can create or resolve a duplicate in the other rows too.
        for current in self._all_rows():
            self._validate(current)
        self.changed.emit()

    def _validate(self, row: _HotkeyRow) -> None:
        chord = row.edit.chord
        row.token = token = object()
        if not chord:
            self._show_neutral(row, _("No shortcut: use the menu"))
            return
        if any(other is not row and other.edit.chord == chord for other in self._all_rows()):
            self._show_status(row, "duplicate")
            return
        self._show_neutral(row, _("Checking..."))

        def current() -> bool:
            # Still the latest check of a row that still exists (load() rebuilds the rows).
            return row.token is token and any(existing is row for existing in self._all_rows())

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

        name = row.name
        self._shell.bridge.run_async(lambda: self._shell.controller.check_hotkey(chord, name), done, failed)

    def _other_chords(self, row: _HotkeyRow) -> set[str]:
        return {other.edit.chord for other in self._all_rows() if other is not row}

    def _show_status(self, row: _HotkeyRow, result: HotkeyCheck) -> None:
        if result == "engine_owned":
            self._show_neutral(row, texts.hotkey_check_labels()[result])
            return
        row.result = result
        row.status.setText(texts.hotkey_check_labels()[result])
        # Back to the normal text role first: with PlaceholderText (left by _show_neutral) Qt
        # paints the stylesheet color at half opacity, which made the problems unreadable.
        row.status.setForegroundRole(QPalette.ColorRole.WindowText)
        row.status.setStyleSheet(f"color: {_status_color(self, result == 'ok')};")
        row.status.setToolTip(texts.hotkey_rule() if result == "invalid" else "")

    def _show_neutral(self, row: _HotkeyRow, text: str) -> None:
        row.result = None
        row.status.setText(text)
        row.status.setStyleSheet("")
        row.status.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        row.status.setToolTip("")

    def changeEvent(self, event: QEvent) -> None:
        # Light/dark switch: the green/orange status colors are picked per palette.
        if event.type() == QEvent.Type.PaletteChange:
            for row in self._all_rows():
                if row.result is not None:
                    self._show_status(row, row.result)
        super().changeEvent(event)


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
    loaded_volume: float = 1.0  # as loaded: kept when the slider still shows it (no false edits)


class SoundsPage(QWidget):
    changed = Signal()

    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._loaded_master_volume = 0.0
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
            self._loaded_master_volume = settings.master_volume
            self.master_volume.setValue(round(settings.master_volume * 100))
            self.master_volume_label.setText(f"{self.master_volume.value()}%")
            for cue, row in self.rows.items():
                cue_settings = settings.cues.get(cue, CueSettings())
                row.enabled.setChecked(cue_settings.enabled)
                row.source.setCurrentIndex(max(row.source.findData(cue_settings.source), 0))
                row.preset.setCurrentIndex(max(row.preset.findData(cue_settings.preset), 0))
                row.file.setText(cue_settings.file)
                row.file.setToolTip(cue_settings.file)
                row.loaded_volume = cue_settings.volume
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
            volume=_slider_volume(row.volume, row.loaded_volume),
        )

    def collect(self) -> tuple[bool, float, dict[CueName, CueSettings]]:
        cues = {cue: self.cue_settings(row) for cue, row in self.rows.items()}
        master = _slider_volume(self.master_volume, self._loaded_master_volume)
        return self.cues_enabled.isChecked(), master, cues

    def _sync_row(self, row: _CueRow) -> None:
        is_file = row.source.currentData() == "file"
        row.stack.setCurrentIndex(1 if is_file else 0)
        master_on = self.cues_enabled.isChecked()
        row.enabled.setEnabled(master_on)
        on = master_on and row.enabled.isChecked()
        for widget in (row.source, row.stack, row.volume, row.volume_label):
            widget.setEnabled(on)
        # Preview stays available: it plays even when muted or disabled (contract).

    def _on_master_toggled(self, enabled: bool) -> None:
        for row in self.rows.values():
            self._sync_row(row)
        self.changed.emit()

    def _choose_file(self, row: _CueRow) -> None:
        start = row.file.text() or str(home_dir() or "")
        path, _filter = QFileDialog.getOpenFileName(self, _("Choose a sound file"), start, _("WAV audio") + " (*.wav)")
        if path:
            row.file.setText(str(Path(path)))
            row.file.setToolTip(str(Path(path)))
            self.changed.emit()

    def _preview(self, row: _CueRow) -> None:
        settings = self.cue_settings(row)
        master = _slider_volume(self.master_volume, self._loaded_master_volume)
        controller = self._shell.controller
        self._shell.bridge.run_serial(lambda: controller.preview_cue(row.cue, settings, master))


# ---------------------------------------------------------------------- Assistant page


class AssistantPage(QWidget):
    """The assistant's settings. The personality text and the Desktop profile actions act at
    once (they are files, not settings); everything else is applied with the dialog."""

    changed = Signal()
    _profile_seen = Signal(object)  # ProfileStatus | None, from the service's worker

    def __init__(self, shell: Shell | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._loaded = AssistantSettings()
        self._loaded_custom = ""  # the written personality as it is on disk
        self._unsubscribe: Callable[[], None] | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.mode = _option_combo(texts.assistant_mode_labels())
        form.addRow(_("Assistant mode:"), self.mode)
        self.desktop_conversation = QComboBox()
        self.desktop_conversation.setEditable(True)
        self.desktop_conversation.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.desktop_conversation.setPlaceholderText(_("Exact title in the Claude Desktop sidebar"))
        self.desktop_conversation_label = QLabel(_("Desktop conversation:"))
        form.addRow(self.desktop_conversation_label, self.desktop_conversation)
        self.model = _option_combo(texts.assistant_model_labels())
        form.addRow(_("Model:"), self.model)
        self.effort = _option_combo(texts.assistant_effort_labels())
        form.addRow(_("Effort:"), self.effort)
        self.model_hint = _secondary(QLabel())
        form.addRow("", self.model_hint)
        self.new_model = _option_combo(texts.assistant_model_labels())
        form.addRow(_("New conversations use:"), self.new_model)
        self.new_effort = _option_combo(texts.assistant_effort_labels())
        form.addRow(_("With the effort:"), self.new_effort)
        self.permission = _option_combo(texts.assistant_permission_labels())
        form.addRow(_("Permissions:"), self.permission)
        self.permission_warning = _secondary(
            QLabel(
                _(
                    "Full access lets the conversations VoiceMate starts change any file and run any "
                    "command on this computer without asking."
                )
            )
        )
        form.addRow("", self.permission_warning)
        self.answer_delivery = _option_combo(texts.answer_delivery_labels())
        form.addRow(_("Answers from other conversations:"), self.answer_delivery)
        self.speak_replies = QCheckBox(_("Speak the replies"))
        self.speak_replies.setToolTip(_("VoiceMate reads the spoken part of each reply aloud."))
        form.addRow("", self.speak_replies)
        self.voice_source = _option_combo(texts.voice_source_labels())
        self.voice_source.setToolTip(
            _(
                "Automatic: the engine's voice, or the voices of Windows when the engine has none. "
                "The voices of Windows need no setup and speak every language installed in Windows "
                "(Settings, Time and language, Speech)."
            )
        )
        form.addRow(_("Voice source:"), self.voice_source)
        self.voice = _voice_combo(_("Default voice"), _("Load the voices, then type part of a name to find one."))
        self.load_voices_button = QPushButton(_("Load voices"))
        self.test_voice_button = QPushButton(_("Test"))
        self.test_voice_button.setToolTip(_("Speaks a short sentence with the chosen voice."))
        for button in (self.load_voices_button, self.test_voice_button):
            button.setAutoDefault(False)
        assistant_voice_row = QHBoxLayout()
        assistant_voice_row.addWidget(self.voice, 1)
        assistant_voice_row.addWidget(self.load_voices_button)
        assistant_voice_row.addWidget(self.test_voice_button)
        form.addRow(_("Assistant voice:"), assistant_voice_row)
        self.voice_status = _secondary(QLabel())
        self.voice_status.setVisible(False)
        form.addRow("", self.voice_status)
        self._voice_busy = False
        self.voice_commands = QCheckBox(_("Understand voice commands in typed text"))
        form.addRow("", self.voice_commands)
        self.expressions = _option_combo(texts.expressions_labels())
        form.addRow(_("Expression tags:"), self.expressions)
        self.user_name = QLineEdit()
        form.addRow(_("Your name:"), self.user_name)
        layout.addLayout(form)

        # --- personality
        persona = QGroupBox(_("Personality"))
        persona_form = QFormLayout(persona)
        persona_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.personality_source = _option_combo(texts.personality_source_labels())
        persona_form.addRow(_("Source:"), self.personality_source)
        self.vault = QComboBox()
        self.vault.setEditable(True)
        self.vault.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.vault.setPlaceholderText(_("The folder of your notes vault"))
        self.vault_browse = QPushButton(_("Browse..."))
        vault_row = QHBoxLayout()
        vault_row.addWidget(self.vault, 1)
        vault_row.addWidget(self.vault_browse)
        self.vault_label = QLabel(_("Vault folder:"))
        persona_form.addRow(self.vault_label, vault_row)
        self.vault_warning = _secondary(QLabel())
        persona_form.addRow("", self.vault_warning)
        self.personality_file = QLineEdit()
        self.personality_file.setPlaceholderText(_("Optional Markdown file"))
        self.browse = QPushButton(_("Browse..."))
        self.clear = QPushButton(_("Clear"))
        file_row = QHBoxLayout()
        file_row.addWidget(self.personality_file, 1)
        file_row.addWidget(self.browse)
        file_row.addWidget(self.clear)
        self.personality_file_label = QLabel(_("Personality file:"))
        persona_form.addRow(self.personality_file_label, file_row)
        self.custom_hint = _secondary(
            QLabel(
                _(
                    "Describe how the assistant should be: its tone, its humor, how formal it is "
                    "and how it addresses you."
                )
            )
        )
        persona_form.addRow(self.custom_hint)
        self.custom_text = QPlainTextEdit()
        self.custom_text.setAccessibleName(_("Your personality text"))
        self.custom_text.setMinimumHeight(self.custom_text.fontMetrics().lineSpacing() * 5 + 16)
        persona_form.addRow(self.custom_text)
        self.custom_save = QPushButton(_("Save"))
        self.custom_save.setAutoDefault(False)
        self.custom_status = _secondary(QLabel())
        save_row = QHBoxLayout()
        save_row.addWidget(self.custom_status, 1)
        save_row.addWidget(self.custom_save)
        persona_form.addRow(save_row)
        layout.addWidget(persona)

        # --- skill
        skill = QGroupBox(_("Skill"))
        skill_form = QFormLayout(skill)
        skill_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.skill_language = _option_combo(texts.skill_language_labels())
        skill_form.addRow(_("Skill language:"), self.skill_language)
        self.custom_skill_dir = QLineEdit()
        self.custom_skill_dir.setPlaceholderText(_("Optional folder with a SKILL.md"))
        self.skill_browse = QPushButton(_("Browse..."))
        self.skill_clear = QPushButton(_("Clear"))
        skill_row = QHBoxLayout()
        skill_row.addWidget(self.custom_skill_dir, 1)
        skill_row.addWidget(self.skill_browse)
        skill_row.addWidget(self.skill_clear)
        skill_form.addRow(_("Custom skill:"), skill_row)
        layout.addWidget(skill)

        # --- Claude Desktop profile
        profile = QGroupBox(_("Claude Desktop profile"))
        profile_layout = QVBoxLayout(profile)
        self.profile_status = _secondary(QLabel(_("Loaded when the assistant starts.")))
        profile_layout.addWidget(self.profile_status)
        self.profile_auto_update = QCheckBox(_("Check for a newer profile when the assistant starts"))
        profile_layout.addWidget(self.profile_auto_update)
        buttons = QHBoxLayout()
        self.profile_check = QPushButton(_("Check now"))
        self.profile_import = QPushButton(_("Import..."))
        self.profile_export = QPushButton(_("Export..."))
        self.profile_default = QPushButton(_("Use default"))
        for button in (self.profile_check, self.profile_import, self.profile_export, self.profile_default):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        buttons.addStretch(1)
        profile_layout.addLayout(buttons)
        layout.addWidget(profile)

        self.hint = _secondary(
            QLabel(
                _(
                    "The assistant is a Claude Code conversation that VoiceMate starts, or a Claude Desktop "
                    "conversation you choose. Changing the mode, the effort, the permissions or the skill "
                    "restarts it."
                )
            )
        )
        layout.addWidget(self.hint)
        layout.addStretch(1)

        for combo in (
            self.mode,
            self.model,
            self.effort,
            self.new_model,
            self.new_effort,
            self.permission,
            self.answer_delivery,
            self.expressions,
            self.personality_source,
            self.skill_language,
        ):
            combo.currentIndexChanged.connect(lambda _index: self._sync())
            combo.currentIndexChanged.connect(self.changed)
        self.desktop_conversation.currentTextChanged.connect(self.changed)
        self.vault.currentTextChanged.connect(lambda _text: self._sync_vault())
        self.vault.currentTextChanged.connect(self.changed)
        self.voice_commands.toggled.connect(self.changed)
        self.speak_replies.toggled.connect(self.changed)
        self.speak_replies.toggled.connect(lambda _checked: self._sync())
        self.voice_source.currentIndexChanged.connect(self.changed)
        self.voice_source.currentIndexChanged.connect(lambda _index: self._source_changed())
        self.voice.currentTextChanged.connect(self.changed)
        self.load_voices_button.clicked.connect(lambda: self._ask_engine(test=False))
        self.test_voice_button.clicked.connect(lambda: self._ask_engine(test=True))
        self.profile_auto_update.toggled.connect(self.changed)
        self.user_name.textChanged.connect(self.changed)
        self.personality_file.textChanged.connect(self.changed)
        self.custom_skill_dir.textChanged.connect(self.changed)
        self.browse.clicked.connect(self._choose_file)
        self.clear.clicked.connect(self.personality_file.clear)
        self.vault_browse.clicked.connect(self._choose_vault)
        self.skill_browse.clicked.connect(self._choose_skill_dir)
        self.skill_clear.clicked.connect(self.custom_skill_dir.clear)
        self.custom_save.clicked.connect(self.save_custom_personality)
        self.custom_text.textChanged.connect(self._custom_text_changed)
        self.profile_check.clicked.connect(self._check_profile)
        self.profile_import.clicked.connect(self._import_profile)
        self.profile_export.clicked.connect(self._export_profile)
        self.profile_default.clicked.connect(self._default_profile)
        self._profile_seen.connect(self._show_profile)
        for widget, name in (
            (self.mode, _("Assistant mode")),
            (self.desktop_conversation, _("Desktop conversation")),
            (self.model, _("Model")),
            (self.effort, _("Effort")),
            (self.new_model, _("Model of new conversations")),
            (self.new_effort, _("Effort of new conversations")),
            (self.permission, _("Permissions")),
            (self.answer_delivery, _("Answers from other conversations")),
            (self.voice_source, _("Voice source")),
            (self.voice, _("Assistant voice")),
            (self.expressions, _("Expression tags")),
            (self.user_name, _("Your name")),
            (self.personality_source, _("Personality")),
            (self.vault, _("Vault folder")),
            (self.personality_file, _("Personality file")),
            (self.skill_language, _("Skill language")),
            (self.custom_skill_dir, _("Custom skill")),
        ):
            widget.setAccessibleName(name)
        if shell is not None:
            service = shell.controller.assistant()
            self._unsubscribe = service.subscribe(lambda snapshot: self._profile_seen.emit(snapshot.profile))
            self.destroyed.connect(lambda _obj=None, stop=self._unsubscribe: stop())

    def load(self, settings: CompanionSettings) -> None:
        assistant = settings.assistant
        self._loaded = assistant
        with QSignalBlocker(self):
            _select(self.mode, assistant.mode, texts.assistant_mode_labels())
            _select(self.model, assistant.model, texts.assistant_model_labels())
            _select(self.effort, assistant.effort, texts.assistant_effort_labels())
            _select(self.new_model, assistant.new_conversation_model, texts.assistant_model_labels())
            _select(self.new_effort, assistant.new_conversation_effort, texts.assistant_effort_labels())
            _select(self.permission, assistant.permission_mode, texts.assistant_permission_labels())
            _select(self.answer_delivery, assistant.answer_delivery, texts.answer_delivery_labels())
            self.voice_commands.setChecked(assistant.voice_commands)
            self.speak_replies.setChecked(assistant.speak_replies)
            _select(self.voice_source, assistant.voice_source, texts.voice_source_labels())
            _fill_voices(self.voice, (), assistant.voice, _("Default voice"))
            _select(self.expressions, assistant.expressions, texts.expressions_labels())
            self.user_name.setText(assistant.user_name)
            _select(self.personality_source, assistant.personality_source, texts.personality_source_labels())
            self.personality_file.setText(assistant.personality_file)
            self._fill_vaults(assistant.obsidian_vault)
            _select(self.skill_language, assistant.skill_language, texts.skill_language_labels())
            self.custom_skill_dir.setText(assistant.custom_skill_dir)
            self.profile_auto_update.setChecked(assistant.desktop_profile_auto_update)
            self.desktop_conversation.clear()
            self.desktop_conversation.setEditText(assistant.desktop_conversation)
            self._loaded_custom = personality.read_custom()
            self.custom_text.setPlainText(self._loaded_custom)
        self.custom_status.setText("")
        self._show_voice_status(None)
        self._fill_conversations(assistant.desktop_conversation)
        self._sync()

    def collect(self) -> AssistantSettings:
        """The loaded `[assistant]` table with this page's edits (the hotkeys are the Hotkeys tab's)."""
        mode: AssistantMode = self.mode.currentData()
        model: AssistantModel = self.model.currentData()
        effort: AssistantEffort = self.effort.currentData()
        new_model: AssistantModel = self.new_model.currentData()
        new_effort: AssistantEffort = self.new_effort.currentData()
        permission: AssistantPermission = self.permission.currentData()
        delivery: AnswerDelivery = self.answer_delivery.currentData()
        voice_source: AssistantVoiceSource = self.voice_source.currentData()
        expressions: ExpressionsMode = self.expressions.currentData()
        source: PersonalitySource = self.personality_source.currentData()
        skill_language: SkillLanguage = self.skill_language.currentData()
        vault = str(self.vault.currentText()).strip()
        return replace(
            self._loaded,
            mode=mode,
            model=model,
            effort=effort,
            new_conversation_model=new_model,
            new_conversation_effort=new_effort,
            permission_mode=permission,
            answer_delivery=delivery,
            speak_replies=self.speak_replies.isChecked(),
            voice_source=voice_source,
            voice=_combo_voice_id(self.voice),
            voice_commands=self.voice_commands.isChecked(),
            expressions=expressions,
            user_name=self.user_name.text().strip(),
            personality_source=source,
            personality_file=self.personality_file.text().strip(),
            obsidian_vault=vault if source == "file" else self._loaded.obsidian_vault,
            desktop_conversation=self.desktop_conversation.currentText().strip(),
            skill_language=skill_language,
            custom_skill_dir=self.custom_skill_dir.text().strip(),
            desktop_profile_auto_update=self.profile_auto_update.isChecked(),
        )

    def _sync(self) -> None:
        mode = self.mode.currentData()
        enabled = mode != "off"
        for widget in (
            self.desktop_conversation,
            self.model,
            self.effort,
            self.new_model,
            self.new_effort,
            self.permission,
            self.answer_delivery,
            self.speak_replies,
            self.voice_commands,
            self.expressions,
            self.user_name,
            self.personality_source,
            self.vault,
            self.vault_browse,
            self.personality_file,
            self.browse,
            self.clear,
            self.custom_text,
            self.custom_save,
            self.skill_language,
            self.custom_skill_dir,
            self.skill_browse,
            self.skill_clear,
        ):
            widget.setEnabled(enabled)
        voiced = enabled and self.speak_replies.isChecked()
        self.voice_source.setEnabled(voiced)
        self.voice.setEnabled(voiced)
        for button in (self.load_voices_button, self.test_voice_button):
            button.setEnabled(voiced and not self._voice_busy and self._shell is not None)
        desktop = mode == "desktop"
        self.desktop_conversation.setVisible(desktop)
        self.desktop_conversation_label.setVisible(desktop)
        self.model_hint.setText(
            _(
                "VoiceMate cannot set the model of a Desktop conversation: it asks the conversation "
                "to switch its own model and effort."
            )
            if desktop
            else _("A new model applies from the next answer; a new effort restarts the conversation.")
        )
        self.permission_warning.setVisible(self.permission.currentData() == "bypassPermissions")
        source = self.personality_source.currentData()
        file_widgets: tuple[QWidget, ...] = (
            self.vault_label,
            self.vault,
            self.vault_browse,
            self.personality_file_label,
            self.personality_file,
            self.browse,
            self.clear,
        )
        for shown in file_widgets:
            shown.setVisible(source == "file")
        custom_widgets: tuple[QWidget, ...] = (self.custom_hint, self.custom_text, self.custom_save, self.custom_status)
        for shown in custom_widgets:
            shown.setVisible(source == "custom")
        self._sync_vault()

    def _sync_vault(self) -> None:
        problem = personality.vault_problem(self.vault.currentText().strip())
        self.vault_warning.setText(
            {
                "missing": _("This folder does not exist."),
                "no_obsidian": _("This folder has no .obsidian folder: it may not be an Obsidian vault."),
            }.get(problem, "")
        )
        self.vault_warning.setVisible(bool(problem) and self.personality_source.currentData() == "file")

    # ------------------------------------------------------------------ voice

    def _ask_engine(self, *, test: bool) -> None:
        """Load voices / Test: ask the engine (its voice as it runs now), off the GUI thread."""
        if self._shell is None:
            return
        controller = self._shell.controller
        voice = _combo_voice_id(self.voice)
        source: AssistantVoiceSource = self.voice_source.currentData()
        self._voice_busy = True
        self._sync()
        self._show_voice_status(None, _("Looking for the voices..."))

        def work() -> TtsProbe:
            return controller.test_assistant_voice(voice, source) if test else controller.assistant_voices(source)

        def done(probe: TtsProbe) -> None:
            self._voice_busy = False
            if probe.voices:
                _fill_voices(self.voice, probe.voices, _combo_voice_id(self.voice), _("Default voice"))
            self._show_voice_status(probe)
            self._sync()

        def failed(exc: BaseException) -> None:
            self._voice_busy = False
            self._show_voice_status(TtsProbe(False, str(exc) or type(exc).__name__))
            self._sync()

        self._shell.bridge.run_async(work, done, failed)

    def _source_changed(self) -> None:
        """Another source lists other voices: the picked one belongs to the old source."""
        _fill_voices(self.voice, (), "", _("Default voice"))
        self._show_voice_status(None)

    def _show_voice_status(self, probe: TtsProbe | None, busy_text: str = "") -> None:
        if busy_text:
            self.voice_status.setStyleSheet("")
            self.voice_status.setText(busy_text)
        elif probe is None:
            self.voice_status.setStyleSheet("")
            self.voice_status.setText("")
        else:
            self.voice_status.setStyleSheet(f"color: {_status_color(self, probe.ok)};")
            self.voice_status.setText(probe.message)
        self.voice_status.setVisible(bool(self.voice_status.text()))

    def _fill_vaults(self, chosen: str) -> None:
        """The saved vault, or the vaults Obsidian knows as suggestions."""
        self.vault.clear()
        for path in personality.obsidian_vaults():
            self.vault.addItem(str(path))
        if chosen:
            self.vault.setEditText(chosen)
        elif self.vault.count():
            self.vault.setCurrentIndex(0)  # a suggestion: saved with the next apply
        else:
            self.vault.setEditText("")

    def _fill_conversations(self, chosen: str) -> None:
        """The titles of recent conversations, read off the GUI thread."""
        if self._shell is None:
            return

        def titles() -> list[str]:
            return list(dict.fromkeys(info.title for info in list_conversations(40) if info.title))

        def show(found: list[str]) -> None:
            current = self.desktop_conversation.currentText()
            with QSignalBlocker(self.desktop_conversation):
                self.desktop_conversation.clear()
                self.desktop_conversation.addItems(found)
                self.desktop_conversation.setEditText(current or chosen)

        self._shell.bridge.run_async(titles, show, lambda _exc: None)

    # ------------------------------------------------------------------ files

    def _start_folder(self) -> str:
        vault = str(self.vault.currentText()).strip()
        if vault and Path(vault).is_dir():
            return vault
        return str(home_dir() or "")

    def _choose_file(self) -> None:
        start = self.personality_file.text() or self._start_folder()
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Choose a personality file"), start, _("Markdown") + " (*.md)"
        )
        if path:
            self.personality_file.setText(str(Path(path)))

    def _choose_vault(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("Choose your vault folder"), self._start_folder())
        if path:
            self.vault.setEditText(str(Path(path)))

    def _choose_skill_dir(self) -> None:
        start = self.custom_skill_dir.text() or str(home_dir() or "")
        path = QFileDialog.getExistingDirectory(self, _("Choose a folder with a SKILL.md"), start)
        if path:
            self.custom_skill_dir.setText(str(Path(path)))

    def _custom_text_changed(self) -> None:
        self.custom_status.setText("")
        self.changed.emit()  # OK and Apply save it too

    def custom_personality_dirty(self) -> bool:
        return bool(self.custom_text.toPlainText() != self._loaded_custom)

    def flush_custom_personality(self) -> bool:
        """Save the written personality when it changed (OK and Apply call this). False: it
        could not be saved (the reason is shown under the text)."""
        return not self.custom_personality_dirty() or self.save_custom_personality()

    def save_custom_personality(self) -> bool:
        text = self.custom_text.toPlainText()
        try:
            personality.save_custom(text)
        except (OSError, ValueError) as exc:
            self.custom_status.setText(_("Not saved: {reason}").format(reason=exc))
            return False
        self._loaded_custom = text
        self.custom_status.setText(_("Saved."))
        self.changed.emit()
        if self._shell is not None:
            self._shell.controller.assistant().refresh_context()
        return True

    # ------------------------------------------------------------------ Desktop profile

    def _show_profile(self, status: ProfileStatus | None) -> None:
        if status is None:
            self.profile_status.setText(_("Loaded when the assistant starts."))
            return
        line = _("In use: version {version}, {source}.").format(
            version=status.version, source=texts.profile_source_label(status.source)
        )
        if status.checking:
            line += " " + _("Checking for a newer one...")
        if status.error:
            line += "\n" + _("Problem: {error}").format(error=status.error)
        self.profile_status.setText(line)

    def _service(self) -> AssistantService | None:
        return self._shell.controller.assistant() if self._shell is not None else None

    def _check_profile(self) -> None:
        service = self._service()
        if service is not None:
            service.check_profile()

    def _import_profile(self) -> None:
        service = self._service()
        if service is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Import a Claude Desktop profile"), str(home_dir() or ""), _("Profile") + " (*.json)"
        )
        if path:
            service.import_profile(str(Path(path)))

    def _export_profile(self) -> None:
        service = self._service()
        if service is None:
            return
        start = str((home_dir() or Path()) / "claude-desktop.json")
        path, _filter = QFileDialog.getSaveFileName(
            self, _("Export the Claude Desktop profile"), start, _("Profile") + " (*.json)"
        )
        if path:
            service.export_profile(str(Path(path)))

    def _default_profile(self) -> None:
        service = self._service()
        if service is not None:
            service.use_default_profile()


# ---------------------------------------------------------------------- General page


class GeneralPage(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None, *, platform: str = sys.platform) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        app_form = QFormLayout()
        app_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._app_form = app_form
        self.language = _option_combo(texts.language_labels())
        app_form.addRow(_("Language:"), self.language)
        # Shown by the dialog while the selected language differs from the running one.
        self.language_hint = _secondary(QLabel(_("Takes effect after VoiceMate restarts.")))
        self.language_hint.setWordWrap(False)  # one short line; wrapping in a form row misplaces it
        app_form.addRow("", self.language_hint)
        app_form.setRowVisible(self.language_hint, False)
        self.dictation_language = _option_combo(texts.dictation_language_labels())
        app_form.addRow(_("Dictation language:"), self.dictation_language)
        # Short lines only: a wrapped label in a form row inside a tab page is clipped (the
        # tab stack sizes the page without height-for-width), so the longer explanation is
        # the tooltip and the visible hint never wraps.
        self.dictation_hint = _secondary(QLabel())
        self.dictation_hint.setWordWrap(False)
        app_form.addRow("", self.dictation_hint)
        self.notify_level = _option_combo(texts.notify_level_labels())
        app_form.addRow(_("Notifications:"), self.notify_level)
        self.start_at_login = QCheckBox(_("Start VoiceMate when I sign in"))
        app_form.addRow("", self.start_at_login)
        # Windows only: Windows 11 hides new tray icons in the overflow behind the arrow.
        self.tray_icon_visible: QCheckBox | None = None
        if platform == "win32":
            self.tray_icon_visible = QCheckBox(_("Always show the VoiceMate icon on the taskbar"))
            self.tray_icon_visible.setToolTip(
                _(
                    "Shows the icon with the recording and transcription state next to the clock, "
                    "instead of hiding it behind the arrow. An icon hidden in the Windows taskbar "
                    "settings stays hidden until you turn this off, click Apply and turn it on again."
                )
            )
            self.tray_icon_visible.toggled.connect(self.changed)
            app_form.addRow("", self.tray_icon_visible)
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

        stt_box = QGroupBox(_("Transcription"))
        stt_form = QFormLayout(stt_box)
        stt_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.stt_mode = _option_combo(texts.stt_mode_labels())
        self.stt_mode.setToolTip(
            _(
                "CPU leaves the graphics card free for other programs. GPU is faster. A mode that is "
                "not installed yet is installed when you apply (VoiceMate asks first). Changing it "
                "restarts the engine."
            )
        )
        stt_form.addRow(_("Run on:"), self.stt_mode)
        self.stt_cpu_model = _option_combo(texts.stt_model_labels())
        stt_form.addRow(_("CPU model:"), self.stt_cpu_model)
        self.stt_gpu_model = _option_combo(texts.stt_model_labels())
        stt_form.addRow(_("GPU model:"), self.stt_gpu_model)
        self._stt_box = stt_box
        layout.addWidget(stt_box)
        layout.addStretch(1)

        for combo in (
            self.language,
            self.dictation_language,
            self.notify_level,
            self.engine_mode,
            self.wsl_restart_policy,
            self.stt_mode,
            self.stt_cpu_model,
            self.stt_gpu_model,
        ):
            combo.currentIndexChanged.connect(self.changed)
        self.engine_mode.currentIndexChanged.connect(lambda _index: self._sync_mode())
        self.dictation_language.currentIndexChanged.connect(lambda _index: self._sync_dictation_hint())
        self.start_at_login.toggled.connect(self.changed)
        self.wsl_distro.textChanged.connect(self.changed)
        self.engine_dir.textChanged.connect(self.changed)
        for widget, name in (
            (self.language, _("Language")),
            (self.dictation_language, _("Dictation language")),
            (self.notify_level, _("Notifications")),
            (self.engine_mode, _("Mode")),
            (self.wsl_distro, _("WSL distro")),
            (self.engine_dir, _("Engine folder")),
            (self.wsl_restart_policy, _("Restart WSL when audio fails")),
            (self.stt_mode, _("Transcription")),
            (self.stt_cpu_model, _("CPU model")),
            (self.stt_gpu_model, _("GPU model")),
        ):
            widget.setAccessibleName(name)

    def load(self, settings: CompanionSettings) -> None:
        with QSignalBlocker(self):
            _select(self.language, settings.language, texts.language_labels())
            _select(self.dictation_language, settings.dictation_language, texts.dictation_language_labels())
            _select(self.notify_level, settings.notify_level, texts.notify_level_labels())
            self.start_at_login.setChecked(settings.start_at_login)
            if self.tray_icon_visible is not None:
                self.tray_icon_visible.setChecked(settings.tray_icon_visible)
            _select(self.engine_mode, settings.engine_mode, texts.engine_mode_labels())
            self.wsl_distro.setText(settings.wsl_distro)
            self.engine_dir.setText(settings.engine_dir)
            _select(self.wsl_restart_policy, settings.wsl_restart_policy, texts.wsl_restart_labels())
            _select(self.stt_mode, settings.stt_mode, texts.stt_mode_labels())
            _select(self.stt_cpu_model, settings.stt_cpu_model, texts.stt_model_labels())
            _select(self.stt_gpu_model, settings.stt_gpu_model, texts.stt_model_labels())
        self._sync_mode()

    def selected_language(self) -> UiLanguage:
        language: UiLanguage = self.language.currentData()
        return language

    def show_language_hint(self, visible: bool) -> None:
        self._app_form.setRowVisible(self.language_hint, visible)

    def apply_to(self, settings: CompanionSettings) -> CompanionSettings:
        language: UiLanguage = self.language.currentData()
        dictation_language: DictationLanguage = self.dictation_language.currentData()
        notify_level: NotifyLevel = self.notify_level.currentData()
        engine_mode: EngineMode = self.engine_mode.currentData()
        policy: WslRestartPolicy = self.wsl_restart_policy.currentData()
        stt_mode: SttMode = self.stt_mode.currentData()
        stt_cpu_model: SttModel = self.stt_cpu_model.currentData()
        stt_gpu_model: SttModel = self.stt_gpu_model.currentData()
        tray_icon_visible = (
            settings.tray_icon_visible if self.tray_icon_visible is None else self.tray_icon_visible.isChecked()
        )
        return replace(
            settings,
            language=language,
            dictation_language=dictation_language,
            notify_level=notify_level,
            start_at_login=self.start_at_login.isChecked(),
            tray_icon_visible=tray_icon_visible,
            engine_mode=engine_mode,
            wsl_distro=self.wsl_distro.text().strip(),
            engine_dir=self.engine_dir.text().strip(),
            wsl_restart_policy=policy,
            stt_mode=stt_mode,
            stt_cpu_model=stt_cpu_model,
            stt_gpu_model=stt_gpu_model,
        )

    def _sync_mode(self) -> None:
        mode = self.engine_mode.currentData()
        for widget in self._wsl_rows:
            widget.setEnabled(mode == "wsl2")
        self.engine_dir.setEnabled(mode != "external")
        self._stt_box.setEnabled(mode != "external")  # the engine is not ours to start
        self._sync_dictation_hint()

    def _sync_dictation_hint(self) -> None:
        """Hint and tooltip for the chosen option. In external mode VoiceMate does not start the
        engine, so the flags are the user's. With "Detect automatically" only the transcription
        is detected: Claude keeps answering in the interface language."""
        external = self.engine_mode.currentData() == "external"
        self.dictation_language.setEnabled(not external)
        if external:
            tooltip = ""
            hint = _("Connect only: set by the engine's own\n--transcription-language and --output-lang flags.")
        elif self.dictation_language.currentData() == "auto":
            tooltip = _(
                "Whisper detects the language of each recording. Claude still answers in the interface language. "
                "Changing it restarts the engine."
            )
            hint = _("Claude still answers in the interface language.")
        else:
            tooltip = _(
                "The language you speak when dictating. It also sets the language of Claude's spoken answers. "
                "Changing it restarts the engine."
            )
            hint = _("Also sets the language of Claude's spoken answers.")
        self.dictation_language.setToolTip(tooltip)
        self.dictation_hint.setToolTip(tooltip)
        self.dictation_hint.setText(hint)


# ---------------------------------------------------------------------- dialog


# ---------------------------------------------------------------------- Voice page


class VoicePage(QWidget):
    """Where spoken answers come from: the engine's own voice, or a custom provider (HTTP)
    that follows docs/tts-provider-protocol.md. The buttons only ask the dialog to talk to the
    provider; it owns the shell and the worker threads."""

    changed = Signal()
    voices_requested = Signal()
    test_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        box = QGroupBox(_("Spoken answers"))
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.tts_engine = _option_combo(texts.tts_engine_labels())
        self.tts_engine.setToolTip(
            _(
                "Where Claude's spoken answers come from. A custom provider is a service you run that follows "
                "the VoiceMate TTS provider protocol (docs/tts-provider-protocol.md). Changing it restarts the engine."
            )
        )
        form.addRow(_("Voice engine:"), self.tts_engine)
        self.tts_url = QLineEdit()
        self.tts_url.setPlaceholderText(DEFAULT_URL)
        self.tts_url.setToolTip(
            _("The address the engine reaches the provider at. Inside WSL, 127.0.0.1 is the WSL side.")
        )
        form.addRow(_("Provider address:"), self.tts_url)
        voice_row = QHBoxLayout()
        self.tts_voice = _voice_combo(
            _("Provider default"), _("Load the voices, then type part of a name to find one, or type a voice id.")
        )
        self.load_voices_button = QPushButton(_("Load voices"))
        voice_row.addWidget(self.tts_voice, 1)
        voice_row.addWidget(self.load_voices_button)
        form.addRow(_("Voice:"), voice_row)
        self.tts_token = QLineEdit()
        self.tts_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.tts_token.setPlaceholderText(_("None"))
        self.tts_token.setToolTip(
            _("Optional. Sent to the provider as a bearer token. It is kept in this computer's settings file.")
        )
        form.addRow(_("Token:"), self.tts_token)
        self.test_button = QPushButton(_("Test voice"))
        self.test_button.setToolTip(_("Speaks a short sentence with the provider and voice above."))
        form.addRow("", self.test_button)
        layout.addWidget(box)
        self._box = box

        self.status = _secondary(QLabel())
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.status.setMinimumHeight(self.status.fontMetrics().lineSpacing() * 3)  # room for a wrapped error
        layout.addWidget(self.status)
        layout.addStretch(1)
        self._probe: tuple[bool, str] | None = None
        self._managed = True
        self._busy = False

        self.tts_engine.currentIndexChanged.connect(self.changed)
        self.tts_engine.currentIndexChanged.connect(lambda _index: self._sync())
        self.tts_url.textChanged.connect(self.changed)
        self.tts_token.textChanged.connect(self.changed)
        self.tts_voice.currentTextChanged.connect(self.changed)
        self.load_voices_button.clicked.connect(self.voices_requested)
        self.test_button.clicked.connect(self.test_requested)
        for widget, name in (
            (self.tts_engine, _("Voice engine")),
            (self.tts_url, _("Provider address")),
            (self.tts_voice, _("Voice")),
            (self.tts_token, _("Token")),
        ):
            widget.setAccessibleName(name)

    def load(self, settings: CompanionSettings) -> None:
        with QSignalBlocker(self):
            _select(self.tts_engine, settings.tts_engine, texts.tts_engine_labels())
            self.tts_url.setText(settings.tts_url)
            self.tts_token.setText(settings.tts_token)
            self.set_voices((), settings.tts_voice)
        self.show_status(None)
        self._sync()

    def apply_to(self, settings: CompanionSettings) -> CompanionSettings:
        engine: TtsChoice = self.tts_engine.currentData()
        return replace(
            settings,
            tts_engine=engine,
            tts_url=self.tts_url.text().strip() or DEFAULT_URL,
            tts_voice=self.voice_id(),
            tts_token=self.tts_token.text().strip(),
        )

    # --- voices ---------------------------------------------------------------------------------------

    def voice_id(self) -> str:
        """The id of the picked voice, or what was typed when it matches none of the entries."""
        return _combo_voice_id(self.tts_voice)

    def set_voices(self, voices: Sequence[Voice], current: str) -> None:
        """Offer the provider's voices (plus the provider default); `current` stays selected, even
        when the provider does not list it."""
        _fill_voices(self.tts_voice, voices, current, _("Provider default"))

    # --- state ----------------------------------------------------------------------------------------

    def set_engine_managed(self, managed: bool) -> None:
        """False in the external engine mode: that engine is not ours to start, so these flags
        would never reach it."""
        self._managed = managed
        self._sync()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._sync()

    def _sync(self) -> None:
        external = self.tts_engine.currentData() == "external"
        self._box.setEnabled(self._managed)
        for widget in (self.tts_url, self.tts_voice, self.tts_token):
            widget.setEnabled(external)
        for button in (self.load_voices_button, self.test_button):
            button.setEnabled(external and not self._busy)

    def show_status(self, probe: TtsProbe | None, busy_text: str = "") -> None:
        """The result of the last call to the provider (None clears it); `busy_text` while one runs."""
        if busy_text:
            self._probe = None
            self.status.setStyleSheet("")
            self.status.setText(_escape(busy_text))
            return
        self._probe = None if probe is None else (probe.ok, probe.message)
        self._paint_status()

    def _paint_status(self) -> None:
        if self._probe is None:
            self.status.setStyleSheet("")
            self.status.setText("")
            return
        ok, message = self._probe
        self.status.setForegroundRole(QPalette.ColorRole.WindowText)
        self.status.setStyleSheet(f"color: {_status_color(self, ok)};")
        self.status.setText(_escape(message))

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "status"):
            self._paint_status()
        super().changeEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, shell: Shell, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._shell = shell
        self._baseline = shell.settings()
        self._busy = False
        self._read_only = False
        self._message_ok: bool | None = None  # the message's color: True ok, False problem, None plain
        self.setWindowTitle(_("VoiceMate Settings"))
        self.setWindowIcon(app_icon())
        # Only the first size: the layout owns the minimum, so the pages never shrink below
        # their contents (an explicit minimum would win over it and clip longer translations,
        # such as Russian cue names on the Sounds tab).
        self.resize(620, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.read_only_note = QLabel(
            _("These settings were saved by a newer version of VoiceMate. They are shown read-only.")
        )
        self.read_only_note.setWordWrap(True)
        layout.addWidget(self.read_only_note)
        self.tabs = QTabWidget()
        self.hotkeys_page = HotkeysPage(shell)
        self.sounds_page = SoundsPage(shell)
        self.voice_page = VoicePage()
        self.assistant_page = AssistantPage(shell)
        self.general_page = GeneralPage()
        self.tabs.addTab(self.hotkeys_page, _("Hotkeys"))
        self.tabs.addTab(self.sounds_page, _("Sounds"))
        self.tabs.addTab(self.voice_page, _("Voice"))
        self.tabs.addTab(self.assistant_page, _("Assistant"))
        self.tabs.addTab(self.general_page, _("General"))
        layout.addWidget(self.tabs, 1)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.RichText)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.hide()
        layout.addWidget(self.message)

        # Our own button texts (translated with the app's catalogs).
        self.buttons = QDialogButtonBox()
        self.ok_button = self.buttons.addButton(_("OK"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = self.buttons.addButton(_("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.apply_button = self.buttons.addButton(_("Apply"), QDialogButtonBox.ButtonRole.ApplyRole)
        self.ok_button.setDefault(True)
        self.buttons.clicked.connect(self._on_button)
        layout.addWidget(self.buttons)

        pages = (self.hotkeys_page, self.sounds_page, self.voice_page, self.assistant_page, self.general_page)
        for page in pages:
            page.changed.connect(self._update_buttons)
        self.voice_page.voices_requested.connect(lambda: self._ask_provider(test=False))
        self.voice_page.test_requested.connect(lambda: self._ask_provider(test=True))
        self.general_page.engine_mode.currentIndexChanged.connect(lambda _index: self._sync_voice_page())
        self.general_page.language.currentIndexChanged.connect(lambda _index: self._sync_language_hint())
        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(self.message.hide)
        self._recolor()
        self.load()

    # ------------------------------------------------------------------ state

    def load(self) -> None:
        """(Re)read the applied settings and the engine's flows into every page. These are
        the baseline: Apply sends only what the user changed relative to them."""
        self._baseline = self._shell.controller.settings()
        self._read_only = settings_read_only(self._baseline)
        self.read_only_note.setVisible(self._read_only)
        snapshot = self._shell.bridge.snapshot
        self.hotkeys_page.load(self._baseline, snapshot, self._read_only)
        self.sounds_page.load(self._baseline)
        self.assistant_page.load(self._baseline)
        self.general_page.load(self._baseline)
        self.voice_page.load(self._baseline)
        self._sync_voice_page()
        self.message.hide()
        self._update_buttons()
        self._sync_language_hint()

    def _sync_voice_page(self) -> None:
        self.voice_page.set_engine_managed(self.general_page.engine_mode.currentData() != "external")

    def _ask_provider(self, *, test: bool) -> None:
        """Voice page buttons: talk to the provider as typed (not yet applied), off the GUI thread."""
        edited = self.collect()
        controller = self._shell.controller
        self.voice_page.set_busy(True)
        self.voice_page.show_status(None, _("Contacting the provider..."))

        def work() -> TtsProbe:
            return controller.test_tts(edited) if test else controller.tts_voices(edited)

        def done(probe: TtsProbe) -> None:
            self.voice_page.set_busy(False)
            if probe.voices:
                self.voice_page.set_voices(probe.voices, self.voice_page.voice_id())
            self.voice_page.show_status(probe)

        def failed(exc: BaseException) -> None:
            self.voice_page.set_busy(False)
            self.voice_page.show_status(TtsProbe(False, str(exc) or type(exc).__name__))

        self._shell.bridge.run_async(work, done, failed)

    def _sync_language_hint(self) -> None:
        selected = self.general_page.selected_language()
        self.general_page.show_language_hint(self._shell.language_needs_restart(selected))

    def collect(self) -> CompanionSettings:
        """The baseline with every edit of this window applied."""
        cues_enabled, master_volume, cues = self.sounds_page.collect()
        edited = replace(
            self._baseline,
            hotkeys=self.hotkeys_page.collect(self._baseline.hotkeys),
            cues_enabled=cues_enabled,
            master_volume=master_volume,
            cues=cues,
            assistant=self.assistant_page.collect().with_hotkeys(self.hotkeys_page.assistant_chords()),
        )
        return self.voice_page.apply_to(self.general_page.apply_to(edited))

    def is_dirty(self) -> bool:
        return self.collect() != self._baseline or self.assistant_page.custom_personality_dirty()

    def _update_buttons(self) -> None:
        dirty = not self._read_only and self.is_dirty()
        self.apply_button.setEnabled(dirty and not self._busy)
        self.ok_button.setEnabled(not self._busy and not self._read_only)
        # While saving, an edit would be reset by the reload at the end: lock the pages.
        # (Enabling a parent never re-enables a child disabled on its own, such as the
        # hotkeys page's Restore defaults in read-only mode.)
        self.hotkeys_page.setEnabled(not self._busy)
        for page in (self.sounds_page, self.assistant_page, self.general_page):
            page.setEnabled(not self._busy and not self._read_only)

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
        if not self.assistant_page.flush_custom_personality():
            self.tabs.setCurrentWidget(self.assistant_page)  # the reason is shown under the text
            self._update_buttons()
            return
        edited, baseline = self.collect(), self._baseline
        if edited == baseline:
            self._update_buttons()
            if close_after:
                self.accept()
            return
        self._busy = True
        self._update_buttons()
        if _stt_changed(edited, baseline):
            # A mode that is not installed yet is installed first (the shell asks).
            self._show_message(_("Checking the transcription mode..."), None)
            self._shell.ensure_stt_ready(edited, lambda ready: self._stt_ready(ready, edited, baseline, close_after))
            return
        self._save(edited, baseline, close_after)

    def _stt_ready(
        self, ready: bool, edited: CompanionSettings, baseline: CompanionSettings, close_after: bool
    ) -> None:
        if ready:
            self._save(edited, baseline, close_after)
            return
        self._applied(
            [
                _(
                    "The transcription mode is not ready (not installed, or another change is under way), so nothing was changed."
                )
            ],
            False,
        )

    def _save(self, edited: CompanionSettings, baseline: CompanionSettings, close_after: bool) -> None:
        self._show_message(_("Saving..."), None)
        controller = self._shell.controller
        new_language = edited.language if edited.language != baseline.language else None

        def work() -> list[str]:
            # Only the user's edits, on top of the LIVE settings: the core may have changed
            # others since this window opened (client_key, a detected engine_dir...).
            return controller.apply_settings(rebase_settings(edited, baseline, controller.settings()))

        self._shell.bridge.run_async(
            work,
            lambda errors: self._applied(errors, close_after, new_language),
            lambda exc: self._applied([_("Unexpected error: {error}").format(error=exc)], False),
        )

    def _applied(self, errors: list[str], close_after: bool, new_language: UiLanguage | None = None) -> None:
        self._busy = False
        if errors:
            items = "".join(f"<li>{_escape(error)}</li>" for error in errors)
            header = _escape(_("The settings were not saved:"))
            self._show_message(f"{header}<ul style='margin: 2px 0 0 -20px;'>{items}</ul>", False)
            self._update_buttons()
            if not self.isVisible() and not self._shell.quitting:
                self.present(reload=False)  # closed while saving: the errors must not get lost
            return
        self._shell.settings_changed()
        if close_after:
            self.message.hide()
            self.accept()
        else:
            self.load()  # the core normalizes what it saves (chords, "~/" in the folder...)
            self._show_message(_escape(_("Settings saved.")), True)
            self._message_timer.start(SAVED_MESSAGE_MS)
        # Saved: a language that selects another catalog only shows after a restart.
        if new_language is not None and self._shell.language_needs_restart(new_language):
            self._shell.offer_language_restart()

    def _show_message(self, html: str, ok: bool | None) -> None:
        self._message_timer.stop()
        self._message_ok = ok
        self._recolor()
        self.message.setText(html)
        self.message.show()

    def _recolor(self) -> None:
        self.read_only_note.setStyleSheet(f"color: {_status_color(self, False)};")
        ok = self._message_ok
        self.message.setStyleSheet("" if ok is None else f"color: {_status_color(self, ok)};")

    def changeEvent(self, event: QEvent) -> None:
        # Light/dark switch: the message colors are picked per palette.
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "message"):
            self._recolor()
        super().changeEvent(event)

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


def rebase_settings(
    edited: CompanionSettings, baseline: CompanionSettings, current: CompanionSettings
) -> CompanionSettings:
    """The user's edits (`edited` vs `baseline`) applied on top of the live `current` settings.

    Untouched fields come from `current`. Hotkeys merge per flow, cues per cue and the
    assistant per field, so an edit of one shortcut, one sound or one assistant setting never
    resets another one changed meanwhile (by voice, or by the switch-mode hotkey).
    """
    changes: dict[str, object] = {}
    for item in fields(CompanionSettings):
        mine, before = getattr(edited, item.name), getattr(baseline, item.name)
        if mine == before:
            continue
        if item.name == "hotkeys":
            changes[item.name] = _rebase_hotkeys(edited.hotkeys, baseline.hotkeys, current.hotkeys)
        elif item.name == "cues":
            merged = dict(current.cues)
            merged.update({cue: value for cue, value in edited.cues.items() if baseline.cues.get(cue) != value})
            changes[item.name] = merged
        elif item.name == "assistant":
            changes[item.name] = _rebase_assistant(edited.assistant, baseline.assistant, current.assistant)
        else:
            changes[item.name] = mine
    return replace(current, **changes)  # type: ignore[arg-type]


def _rebase_assistant(mine: AssistantSettings, before: AssistantSettings, live: AssistantSettings) -> AssistantSettings:
    touched = {
        item.name: getattr(mine, item.name)
        for item in fields(AssistantSettings)
        if getattr(mine, item.name) != getattr(before, item.name)
    }
    return replace(live, **touched)


def _rebase_hotkeys(
    mine: tuple[HotkeyBinding, ...], before: tuple[HotkeyBinding, ...], live: tuple[HotkeyBinding, ...]
) -> tuple[HotkeyBinding, ...]:
    edited = {binding.flow: binding.chord for binding in mine}
    original = {binding.flow: binding.chord for binding in before}
    touched = {flow for flow in edited.keys() | original.keys() if edited.get(flow) != original.get(flow)}
    result: list[HotkeyBinding] = []
    for binding in live:
        if binding.flow not in touched:
            result.append(binding)
        elif binding.flow in edited:  # a cleared shortcut (not in `edited`) drops its binding
            result.append(HotkeyBinding(binding.flow, edited[binding.flow]))
    present = {binding.flow for binding in result}
    result.extend(binding for binding in mine if binding.flow in touched and binding.flow not in present)
    return tuple(result)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _stt_changed(edited: CompanionSettings, baseline: CompanionSettings) -> bool:
    """Only what decides the engine's flags: the model of the mode not in use does not count."""
    return engine_stt(edited) != engine_stt(baseline)
