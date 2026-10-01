"""A field that captures ONE key chord (not a multi-key sequence like QKeySequenceEdit).

Click it, or press Enter/Space while it has focus, then press the new shortcut.
While capturing: Esc cancels (the old chord stays), Backspace/Delete clears, Tab
leaves the field (and cancels). Shortcut overrides are swallowed while capturing,
so a chord such as Alt+S never triggers a button mnemonic instead.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFocusEvent, QFont, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QLineEdit, QWidget

from app.companion.ui.chords import chord_display, chord_from_key, is_modifier_key, modifiers_display
from app.i18n import _

_CLEAR_KEYS = (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
_START_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space)
_OWN_MODIFIER = {
    Qt.Key.Key_Control: Qt.KeyboardModifier.ControlModifier,
    Qt.Key.Key_Alt: Qt.KeyboardModifier.AltModifier,
    Qt.Key.Key_Shift: Qt.KeyboardModifier.ShiftModifier,
    Qt.Key.Key_Meta: Qt.KeyboardModifier.MetaModifier,
}


def _held_modifiers(key: int, modifiers: Qt.KeyboardModifier, pressed: bool) -> Qt.KeyboardModifier:
    """Modifiers held after this modifier key event. Platforms disagree on whether a
    modifier's own press/release event already includes/excludes its flag."""
    try:
        own = _OWN_MODIFIER.get(Qt.Key(key))
    except ValueError:
        own = None
    if own is None:
        return modifiers
    return (modifiers | own) if pressed else (modifiers & ~own)


class HotkeyEdit(QLineEdit):
    chord_changed = Signal(str)  # the new chord ("" = cleared); not emitted for set_chord()
    capture_started = Signal()
    capture_finished = Signal()

    def __init__(self, chord: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chord = chord
        self._capturing = False
        self._editable = True
        self.setReadOnly(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(170)
        self.setAccessibleDescription(_("Press Enter, then the new shortcut. Esc cancels, Backspace clears."))
        self._show_chord()

    # ------------------------------------------------------------------ API

    @property
    def chord(self) -> str:
        return self._chord

    @property
    def capturing(self) -> bool:
        return self._capturing

    def set_chord(self, chord: str) -> None:
        self._chord = chord
        if not self._capturing:
            self._show_chord()

    def set_editable(self, editable: bool) -> None:
        """Read-only mode shows the chord but never captures (engine-owned hotkeys)."""
        self._editable = editable
        if not editable and self._capturing:
            self.finish_capture(None)
        self.setCursor(Qt.CursorShape.PointingHandCursor if editable else Qt.CursorShape.ArrowCursor)

    def start_capture(self) -> None:
        if self._capturing or not self._editable or not self.isEnabled():
            return
        self._capturing = True
        self.setText("")
        self.setPlaceholderText(_("Press the new shortcut..."))
        self._set_italic(True)
        self.capture_started.emit()

    def finish_capture(self, chord: str | None) -> None:
        """End capturing; `chord` None cancels (keeps the old chord)."""
        if not self._capturing:
            return
        self._capturing = False
        self._set_italic(False)
        changed = chord is not None and chord != self._chord
        if chord is not None:
            self._chord = chord
        self._show_chord()
        self.capture_finished.emit()
        if changed:
            self.chord_changed.emit(self._chord)

    # ------------------------------------------------------------------ events

    def event(self, event: QEvent) -> bool:
        if self._capturing and event.type() == QEvent.Type.ShortcutOverride:
            event.accept()  # deliver the key to keyPressEvent, not to a shortcut/mnemonic
            return True
        return bool(super().event(event))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._editable:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.start_capture()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        plain = modifiers == Qt.KeyboardModifier.NoModifier
        if not self._capturing:
            if plain and key in _START_KEYS and self._editable:
                self.start_capture()
                event.accept()
            elif plain and key in _CLEAR_KEYS and self._editable and self._chord:
                self._chord = ""
                self._show_chord()
                self.chord_changed.emit("")
                event.accept()
            else:
                event.ignore()  # Esc closes the dialog, Enter on a read-only field presses the default button
            return
        event.accept()
        if plain and key == Qt.Key.Key_Escape:
            self.finish_capture(None)
        elif plain and key in _CLEAR_KEYS:
            self.finish_capture("")
        elif is_modifier_key(key):
            self.setText(modifiers_display(_held_modifiers(key, modifiers, pressed=True)))
        else:
            chord = chord_from_key(key, event.modifiers(), event.nativeVirtualKey())
            if chord is None:
                self.setText("")
                self.setPlaceholderText(_("This key cannot be used"))
            else:
                self.finish_capture(chord)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if self._capturing:
            self.setText(modifiers_display(_held_modifiers(event.key(), event.modifiers(), pressed=False)))
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        # Popups (e.g. a tooltip) do not count; any real focus change cancels the capture.
        if event.reason() != Qt.FocusReason.PopupFocusReason:
            self.finish_capture(None)
        super().focusOutEvent(event)

    # ------------------------------------------------------------------ helpers

    def _show_chord(self) -> None:
        self.setPlaceholderText(_("No shortcut"))
        self.setText(chord_display(self._chord))

    def _set_italic(self, italic: bool) -> None:
        font = QFont(self.font())
        font.setItalic(italic)
        self.setFont(font)
