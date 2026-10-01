"""Icons drawn with QPainter: the tray glyph per `TrayState`, and the app icon.

Tray icons follow the Windows 11 style: a monochrome microphone in the taskbar's
foreground color, plus a colored badge in the bottom-right corner. Badges differ
by SHAPE (disc, triangle, rounded square, speech bubble) and by their symbol,
never only by color. Every size is painted on its own (no scaling of a big
bitmap), so 16 px stays crisp at 100 % and the 32/48 px variants serve high DPI.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

import app.companion
from app.companion.contract import TrayState

# "light" = white glyph for a dark taskbar; "dark" = black glyph for a light taskbar.
GlyphTone = Literal["light", "dark"]
Badge = Literal[
    "none",
    "octagon",
    "clock",
    "arrows",
    "record",
    "hourglass",
    "bubble",
    "speaker",
    "check",
    "triangle",
    "cross",
]

# Shipped by the packaging stream next to the package (the exe uses the same .ico).
ASSET_ICON: Final = Path(app.companion.__file__).parent / "assets" / "voicemate.ico"
TRAY_SIZES: Final = (16, 20, 24, 32, 40, 48, 64)
APP_ICON_SIZES: Final = (16, 20, 24, 32, 40, 48, 64, 128, 256)
_UNITS: Final = 32.0  # every shape is designed on a 32 x 32 grid

STATE_BADGES: Final[dict[TrayState, Badge]] = {
    "stopped": "octagon",
    "starting": "clock",
    "restarting": "arrows",
    "idle": "none",
    "recording": "record",
    "transcribing": "hourglass",
    "thinking": "bubble",
    "speaking": "speaker",
    "ready": "check",
    "warning": "triangle",
    "error": "cross",
}

_RED: Final = QColor("#E5372C")
_AMBER: Final = QColor("#F2A516")
_VIOLET: Final = QColor("#8B5CF6")
_GREEN: Final = QColor("#1F9D55")
_YELLOW: Final = QColor("#FFC83D")
_GREY: Final = QColor("#8C8C8C")  # mid grey: readable on dark and light taskbars
_WHITE: Final = QColor("#FFFFFF")
_INK: Final = QColor("#1B1B1B")

_BADGE_CENTER: Final = QPointF(23.0, 23.0)
_BADGE_RADIUS: Final = 8.5
_BADGE_GAP: Final = 1.8  # transparent ring that separates the badge from the glyph
_RECORD_RADIUS: Final = 6.4


def taskbar_glyph_tone() -> GlyphTone:
    """Glyph tone for the tray: Windows reads SystemUsesLightTheme (the taskbar's own
    theme, which may differ from the apps' theme); elsewhere the Qt color scheme."""
    if sys.platform == "win32":
        light_taskbar = _windows_taskbar_is_light()
        if light_taskbar is not None:
            return "dark" if light_taskbar else "light"
    hints = QGuiApplication.styleHints()
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Light else "light"


def _windows_taskbar_is_light() -> bool | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "SystemUsesLightTheme")
    except OSError:
        return None
    return bool(value)


def glyph_color(tone: GlyphTone, state: TrayState) -> QColor:
    if state == "stopped":
        return QColor("#9A9A9A") if tone == "light" else QColor("#6B6B6B")
    return QColor(_WHITE) if tone == "light" else QColor("#111111")


def tray_icon(state: TrayState, tone: GlyphTone) -> QIcon:
    icon = QIcon()
    for size in TRAY_SIZES:
        icon.addPixmap(tray_pixmap(state, tone, size))
    return icon


def tray_pixmap(state: TrayState, tone: GlyphTone, size: int) -> QPixmap:
    return state_pixmap(state, glyph_color(tone, state), size)


def state_pixmap(state: TrayState, glyph: QColor, size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """The microphone in `glyph` color with the badge of `state` (also used in the status window)."""
    badge = STATE_BADGES[state]
    pixels = max(int(round(size * device_pixel_ratio)), 1)
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(pixels / _UNITS, pixels / _UNITS)
        stroke = max(2.6, 1.2 * _UNITS / pixels)  # never thinner than ~1.2 device pixels
        _paint_microphone(painter, glyph, center_x=12.0 if badge != "none" else 16.0, stroke=stroke)
        if badge != "none":
            _clear_badge_area(painter, badge)
            _BADGE_PAINTERS[badge](painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def app_icon() -> QIcon:
    """Window / taskbar icon: the packaged `assets/voicemate.ico` (the same icon as the exe
    and the Start menu shortcut), or a drawn microphone when the assets are missing."""
    if ASSET_ICON.is_file():
        packaged = QIcon(str(ASSET_ICON))
        if not packaged.isNull():
            return packaged
    return drawn_app_icon()


def drawn_app_icon() -> QIcon:
    """A white microphone on a blue-violet rounded square, painted at every size."""
    icon = QIcon()
    for size in APP_ICON_SIZES:
        icon.addPixmap(app_pixmap(size))
    return icon


def app_pixmap(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / _UNITS, size / _UNITS)
        gradient = QLinearGradient(0.0, 0.0, _UNITS, _UNITS)
        gradient.setColorAt(0.0, QColor("#3B82F6"))
        gradient.setColorAt(1.0, QColor("#7C3AED"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(QRectF(1.0, 1.0, 30.0, 30.0), 7.0, 7.0)
        painter.translate(3.2, 3.6)
        painter.scale(0.8, 0.8)
        _paint_microphone(painter, _WHITE, center_x=16.0, stroke=max(2.8, 1.4 * _UNITS / size / 0.8))
    finally:
        painter.end()
    return pixmap


def _paint_microphone(painter: QPainter, color: QColor, center_x: float, stroke: float) -> None:
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    # Capsule.
    painter.drawRoundedRect(QRectF(center_x - 5.0, 2.5, 10.0, 16.0), 5.0, 5.0)
    pen = QPen(color, stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Holder: the lower half of an ellipse around the capsule.
    holder = QPainterPath()
    holder.moveTo(center_x - 9.0, 12.0)
    holder.arcTo(QRectF(center_x - 9.0, 3.0, 18.0, 19.0), 180.0, 180.0)
    painter.drawPath(holder)
    # Stem and base.
    painter.drawLine(QPointF(center_x, 22.5), QPointF(center_x, 27.5))
    painter.drawLine(QPointF(center_x - 5.0, 28.5), QPointF(center_x + 5.0, 28.5))
    painter.restore()


def _badge_outline(badge: Badge, grow: float = 0.0) -> QPainterPath:
    center, radius = _BADGE_CENTER, _BADGE_RADIUS + grow
    path = QPainterPath()
    if badge == "triangle":
        top = QPointF(center.x(), center.y() - radius - 0.5 * grow)
        path.addPolygon(
            QPolygonF(
                [
                    top,
                    QPointF(center.x() + radius + 0.6, center.y() + radius * 0.82),
                    QPointF(center.x() - radius - 0.6, center.y() + radius * 0.82),
                    top,
                ]
            )
        )
    elif badge == "record":
        # A smaller solid dot (the usual REC light): its silhouette differs from the discs.
        small = _RECORD_RADIUS + grow
        path.addEllipse(center, small, small)
    elif badge == "octagon":
        points = [
            QPointF(center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle))
            for angle in (math.radians(22.5 + 45.0 * step) for step in range(8))
        ]
        path.addPolygon(QPolygonF([*points, points[0]]))
    elif badge == "cross":
        side = 2.0 * radius - 1.0
        path.addRoundedRect(QRectF(center.x() - side / 2, center.y() - side / 2, side, side), 3.0, 3.0)
    elif badge == "bubble":
        body = QRectF(center.x() - radius, center.y() - radius, 2.0 * radius, 2.0 * radius - 3.5)
        path.addRoundedRect(body, 4.0 + grow, 4.0 + grow)
        tail = QPolygonF(
            [
                QPointF(center.x() - 4.5, body.bottom() - 1.0),
                QPointF(center.x() - 5.5, body.bottom() + 4.0 + grow),
                QPointF(center.x() + 0.5, body.bottom() - 1.0),
            ]
        )
        tail_path = QPainterPath()
        tail_path.addPolygon(tail)
        path = path.united(tail_path)
    else:
        path.addEllipse(center, radius, radius)
    return path


def _clear_badge_area(painter: QPainter, badge: Badge) -> None:
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0))
    painter.drawPath(_badge_outline(badge, grow=_BADGE_GAP))
    painter.restore()


def _fill_badge(painter: QPainter, badge: Badge, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(_badge_outline(badge))


def _symbol_pen(color: QColor, width: float = 2.2) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _paint_record(painter: QPainter) -> None:
    _fill_badge(painter, "record", _RED)


def _paint_clock(painter: QPainter) -> None:
    _fill_badge(painter, "clock", _GREY)
    c = _BADGE_CENTER
    painter.setPen(_symbol_pen(_WHITE, 2.4))
    painter.drawLine(c, QPointF(c.x(), c.y() - 5.2))
    painter.drawLine(c, QPointF(c.x() + 4.2, c.y() + 1.8))


def _paint_arrows(painter: QPainter) -> None:
    # No disc: a thick circular arrow, so "restarting" differs from the "starting" clock by
    # its silhouette (a ring with a gap), not only by the symbol inside.
    c = _BADGE_CENTER
    radius = 6.2
    painter.setPen(_symbol_pen(_GREY, 3.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius), 60 * 16, 270 * 16)
    # Arrow head at the start of the arc (60 degrees), pointing along it.
    angle = math.radians(60.0)
    tip = QPointF(c.x() + radius * math.cos(angle), c.y() - radius * math.sin(angle))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_GREY)
    painter.drawPolygon(QPolygonF([tip + QPointF(-4.6, -2.2), tip + QPointF(3.2, -3.4), tip + QPointF(1.4, 4.4)]))


def _paint_octagon(painter: QPainter) -> None:
    _fill_badge(painter, "octagon", _GREY)
    c = _BADGE_CENTER
    painter.setPen(_symbol_pen(_WHITE, 2.6))
    painter.drawLine(QPointF(c.x() - 4.2, c.y()), QPointF(c.x() + 4.2, c.y()))


def _paint_hourglass(painter: QPainter) -> None:
    _fill_badge(painter, "hourglass", _AMBER)
    c = _BADGE_CENTER
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_INK)
    top = QPolygonF([QPointF(c.x() - 4.2, c.y() - 5.2), QPointF(c.x() + 4.2, c.y() - 5.2), QPointF(c.x(), c.y())])
    bottom = QPolygonF([QPointF(c.x() - 4.2, c.y() + 5.2), QPointF(c.x() + 4.2, c.y() + 5.2), QPointF(c.x(), c.y())])
    painter.drawPolygon(top)
    painter.drawPolygon(bottom)


def _paint_bubble(painter: QPainter) -> None:
    _fill_badge(painter, "bubble", _VIOLET)
    c = _BADGE_CENTER
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_WHITE)
    for dx in (-3.6, 0.0, 3.6):
        painter.drawEllipse(QPointF(c.x() + dx, c.y() - 1.6), 1.25, 1.25)


def _paint_speaker(painter: QPainter) -> None:
    _fill_badge(painter, "speaker", _VIOLET)
    c = _BADGE_CENTER
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_WHITE)
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(c.x() - 5.0, c.y() - 2.0),
                QPointF(c.x() - 2.6, c.y() - 2.0),
                QPointF(c.x() + 0.8, c.y() - 5.0),
                QPointF(c.x() + 0.8, c.y() + 5.0),
                QPointF(c.x() - 2.6, c.y() + 2.0),
                QPointF(c.x() - 5.0, c.y() + 2.0),
            ]
        )
    )
    painter.setPen(_symbol_pen(_WHITE, 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(c.x() - 1.5, c.y() - 3.5, 7.0, 7.0), -50 * 16, 100 * 16)


def _paint_check(painter: QPainter) -> None:
    _fill_badge(painter, "check", _GREEN)
    c = _BADGE_CENTER
    painter.setPen(_symbol_pen(_WHITE, 2.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(c.x() - 4.2, c.y() + 0.2))
    path.lineTo(QPointF(c.x() - 1.2, c.y() + 3.4))
    path.lineTo(QPointF(c.x() + 4.6, c.y() - 3.4))
    painter.drawPath(path)


def _paint_triangle(painter: QPainter) -> None:
    _fill_badge(painter, "triangle", _YELLOW)
    c = _BADGE_CENTER
    painter.setPen(_symbol_pen(_INK, 2.2))
    painter.drawLine(QPointF(c.x(), c.y() - 3.2), QPointF(c.x(), c.y() + 1.6))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_INK)
    painter.drawEllipse(QPointF(c.x(), c.y() + 4.6), 1.25, 1.25)


def _paint_cross(painter: QPainter) -> None:
    _fill_badge(painter, "cross", _RED)
    c = _BADGE_CENTER
    painter.setPen(_symbol_pen(_WHITE, 2.4))
    painter.drawLine(QPointF(c.x() - 3.6, c.y() - 3.6), QPointF(c.x() + 3.6, c.y() + 3.6))
    painter.drawLine(QPointF(c.x() + 3.6, c.y() - 3.6), QPointF(c.x() - 3.6, c.y() + 3.6))


_BADGE_PAINTERS: Final[dict[Badge, Callable[[QPainter], None]]] = {
    "octagon": _paint_octagon,
    "clock": _paint_clock,
    "arrows": _paint_arrows,
    "record": _paint_record,
    "hourglass": _paint_hourglass,
    "bubble": _paint_bubble,
    "speaker": _paint_speaker,
    "check": _paint_check,
    "triangle": _paint_triangle,
    "cross": _paint_cross,
}
