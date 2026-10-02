"""Icons drawn with QPainter: the VoiceMate mark, the tray glyph per `TrayState`, the app icon.

The mark (docs/brand.md) is a person with raised arms: one thick sound-wave stroke whose
outer ends are the arms and whose deep middle V is the body (your voice), with a coral
dot above it as the head (the companion listening). The dot is always coral.

Tray icons follow the Windows 11 style: the mark alone in the taskbar's foreground color
(no tile), plus a colored badge in the bottom-right corner that tells the state. Badges
differ by SHAPE (disc, octagon, triangle, rounded square, speech bubble, ring) and by
their symbol, never only by color. The app icon (exe, windows, installer) puts the mark
on a framed tile: a blue-to-magenta outer square around a deep-indigo inner square.

Every size is painted on its own (no scaling of a big bitmap): below 48 px the mark's
stroke width is a whole number of pixels and its caps, joins and dot land on the pixel
grid, so 16 px stays crisp at 100 % and the bigger variants serve high DPI.
`tools/gen_icon.py` writes the packaged icon from `brand_image`.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

import app.companion
from app.companion.contract import TrayState

# "light" = white glyph for a dark taskbar; "dark" = near-black glyph for a light taskbar.
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
_UNITS: Final = 32.0  # layouts and badges are designed on a 32 x 32 grid
_SNAP_BELOW: Final = 48  # sizes under this snap the mark to the pixel grid

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

CORAL: Final = QColor("#FB7185")  # the head: the same in every state but `stopped`
OUTER_TOP_RIGHT: Final = QColor("#4F6EF7")
OUTER_BOTTOM_LEFT: Final = QColor("#A24FE0")
INNER_TOP: Final = QColor("#2A0FA0")
INNER_BOTTOM: Final = QColor("#3B12B5")

_RED: Final = QColor("#E5372C")
_AMBER: Final = QColor("#F2A516")
_VIOLET: Final = QColor("#8B5CF6")
_GREEN: Final = QColor("#1F9D55")
_YELLOW: Final = QColor("#FFC83D")
_GREY: Final = QColor("#8C8C8C")  # mid grey: readable on dark and light taskbars
_WHITE: Final = QColor("#FFFFFF")
_INK: Final = QColor("#1B1B1B")

# The badges are drawn around (23, 23) with radius 8.5 and placed, a little smaller, in
# the corner: the mark is wide, and a full-size badge would cut its right arm.
_BADGE_CENTER: Final = QPointF(23.0, 23.0)
_BADGE_RADIUS: Final = 8.5
_BADGE_GAP: Final = 1.8  # transparent ring that separates the badge from the glyph
_RECORD_RADIUS: Final = 6.4
_BADGE_PLACE: Final = QPointF(23.8, 23.8)  # where the badge's centre lands
_BADGE_SCALE: Final = 8.1 / 8.5  # its final radius: 8.1 units

Point = tuple[float, float]


@dataclass(frozen=True)
class _MarkShape:
    """The mark in its own units (y grows downwards): the wave's five points
    (left arm, left shoulder, body, right shoulder, right arm), its width and the head."""

    wave: tuple[Point, Point, Point, Point, Point]
    stroke: float
    head: Point
    radius: float

    def bounds(self) -> QRectF:
        half = self.stroke / 2
        left = min(x for x, _ in self.wave) - half
        right = max(x for x, _ in self.wave) + half
        top = min(min(y for _, y in self.wave) - half, self.head[1] - self.radius)
        bottom = max(y for _, y in self.wave) + half
        return QRectF(left, top, right - left, bottom - top)


# From the concept drawing (1024 px canvas / 32): arms end at 17.3, well above the body's
# lowest point (21.9); the head is raised above the shoulders, with a clear gap.
_MARK: Final = _MarkShape(
    wave=((7.0, 17.3), (10.8, 10.3), (16.0, 21.9), (21.2, 10.3), (25.0, 17.3)),
    stroke=3.0,
    head=(16.0, 7.9),
    radius=1.95,
)


@dataclass(frozen=True)
class _PixelMark:
    """The mark placed on an icon, in device pixels."""

    wave: tuple[QPointF, ...]
    stroke: float
    head: QPointF
    radius: float


def _place_mark(box: QRectF, size: int, top_left: bool = False) -> _PixelMark:
    """Fit the mark into `box` (32-unit grid) of a `size` px icon; centred, or pushed to
    the top-left corner (to leave room for a badge). Small sizes snap to the pixel grid."""
    bounds = _MARK.bounds()
    unit = size / _UNITS
    scale = min(box.width() / bounds.width(), box.height() / bounds.height()) * unit
    if top_left:
        origin = QPointF(box.left() * unit, box.top() * unit)
    else:
        origin = QPointF(
            box.center().x() * unit - bounds.width() * scale / 2,
            box.center().y() * unit - bounds.height() * scale / 2,
        )

    def to_pixels(point: Point) -> QPointF:
        return QPointF(origin.x() + (point[0] - bounds.left()) * scale, origin.y() + (point[1] - bounds.top()) * scale)

    wave = [to_pixels(point) for point in _MARK.wave]
    head = to_pixels(_MARK.head)
    stroke = _MARK.stroke * scale
    radius = _MARK.radius * scale
    if size >= _SNAP_BELOW:
        return _PixelMark(tuple(wave), stroke, head, radius)

    # A whole number of pixels for the stroke; with an even width the centre line sits on
    # pixel boundaries, with an odd one on pixel centres, so the caps' edges are crisp.
    stroke = max(round(stroke), 1)
    offset = 0.0 if stroke % 2 == 0 else 0.5

    def snap(value: float) -> float:
        return round(value - offset) + offset

    axis = snap(head.x())
    left_arm = QPointF(snap(wave[0].x()), snap(wave[0].y()))
    left_shoulder = QPointF(snap(wave[1].x()), snap(wave[1].y()))
    body = QPointF(axis, snap(wave[2].y()))
    snapped = (
        left_arm,
        left_shoulder,
        body,
        QPointF(2 * axis - left_shoulder.x(), left_shoulder.y()),
        QPointF(2 * axis - left_arm.x(), left_arm.y()),
    )
    # The head is wider than the stroke (or it reads as a stray pixel) and its diameter has
    # the axis' parity (the stroke's), so its edges are whole pixels too.
    diameter = max(round(2 * radius), stroke + 1)
    if diameter % 2 != stroke % 2:
        diameter += 1
    top = round(head.y() - diameter / 2)
    return _PixelMark(snapped, float(stroke), QPointF(axis, top + diameter / 2), diameter / 2)


Layout = Literal["app", "tray", "tray_badged"]


def _hand(wave: tuple[Point, ...], stroke: float, head: Point, radius: float) -> _PixelMark:
    return _PixelMark(tuple(QPointF(*point) for point in wave), stroke, QPointF(*head), radius)


# Hand-tuned on their own pixel grids where the automatic snap loses the figure: a head
# at least as big as the stroke, raised above the shoulders with a 1 px gap, the body's
# lowest point well below the arms, and the badge's cut-out clear of the right arm.
_PIXEL_MARKS: Final[dict[tuple[Layout, int], _PixelMark]] = {
    ("app", 16): _hand(((3, 10), (5, 6), (8, 12), (11, 6), (13, 10)), 2, (8, 3), 1),
    ("app", 20): _hand(((3, 13), (6, 8), (10, 16), (14, 8), (17, 13)), 2, (10, 4), 2),
    ("app", 24): _hand(((5, 15), (8, 10), (12, 18), (16, 10), (19, 15)), 2, (12, 6), 2),
    ("tray", 16): _hand(((1, 11), (4, 6), (8, 14), (12, 6), (15, 11)), 2, (8, 3), 2),
    ("tray", 20): _hand(((2, 14), (6, 7), (10, 17), (14, 7), (18, 14)), 2, (10, 4), 2),
    ("tray_badged", 16): _hand(((1, 6), (3, 3), (6, 9), (9, 3), (11, 6)), 2, (6, 1), 1),
    ("tray_badged", 20): _hand(((1, 7), (3, 4), (7, 11), (11, 4), (13, 7)), 2, (7, 2), 2),
    ("tray_badged", 24): _hand(((1, 9), (5, 5), (9, 14), (13, 5), (17, 9)), 2, (9, 2), 2),
}


def _paint_mark(painter: QPainter, mark: _PixelMark, wave_color: QColor, head_color: QColor) -> None:
    painter.save()
    pen = QPen(wave_color, mark.stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(mark.wave[0])
    for point in mark.wave[1:]:
        path.lineTo(point)
    painter.drawPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(head_color)
    if mark.radius <= 1.0:  # a 2 px head: a crisp pixel block reads stronger than a blurred disc
        painter.drawRect(
            QRectF(mark.head.x() - mark.radius, mark.head.y() - mark.radius, 2 * mark.radius, 2 * mark.radius)
        )
    else:
        painter.drawEllipse(mark.head, mark.radius, mark.radius)
    painter.restore()


# ---------------------------------------------------------------------- tray glyph


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
    """The mark in `glyph` color with the badge of `state` (also used in the status window)."""
    pixels = max(int(round(size * device_pixel_ratio)), 1)
    pixmap = QPixmap.fromImage(glyph_image(state, glyph, pixels))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def glyph_image(state: TrayState, glyph: QColor, size: int) -> QImage:
    """The tray glyph of `state` at `size` px: the mark without a tile, plus its badge."""
    badge = STATE_BADGES[state]
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if badge == "none":
            mark = _PIXEL_MARKS.get(("tray", size)) or _place_mark(QRectF(0.5, 1.0, 31.0, 30.0), size)
        else:
            # Pushed to the top-left corner and smaller: the badge's cut-out must not reach
            # the right arm.
            mark = _PIXEL_MARKS.get(("tray_badged", size)) or _place_mark(
                QRectF(0.0, 0.5, 22.5, 18.7), size, top_left=True
            )
        head = glyph if state == "stopped" else CORAL  # stopped: the whole glyph is dimmed
        _paint_mark(painter, mark, glyph, head)
        if badge != "none":
            painter.scale(size / _UNITS, size / _UNITS)
            painter.translate(_BADGE_PLACE)
            painter.scale(_BADGE_SCALE, _BADGE_SCALE)
            painter.translate(-_BADGE_CENTER)
            _clear_badge_area(painter, badge)
            _BADGE_PAINTERS[badge](painter)
    finally:
        painter.end()
    return image


# ---------------------------------------------------------------------- app icon


def app_icon() -> QIcon:
    """Window / taskbar icon: the packaged `assets/voicemate.ico` (the same icon as the exe
    and the Start menu shortcut), or the mark drawn here when the assets are missing."""
    if ASSET_ICON.is_file():
        packaged = QIcon(str(ASSET_ICON))
        if not packaged.isNull():
            return packaged
    return drawn_app_icon()


def drawn_app_icon() -> QIcon:
    """The framed tile with the mark, painted at every size."""
    icon = QIcon()
    for size in APP_ICON_SIZES:
        icon.addPixmap(app_pixmap(size))
    return icon


def app_pixmap(size: int) -> QPixmap:
    return QPixmap.fromImage(brand_image(size))


@dataclass(frozen=True)
class _Tile:
    """The app icon's tile in pixels: outer margin, outer corner, frame width, inner corner."""

    margin: float
    corner: float
    frame: float
    inner_corner: float


# At 16 and 20 px the frame is one pixel (a thin bright edge around the dark square) and
# the tile has no margin: a fraction of a pixel would only blur the edge.
_TILES: Final[dict[int, _Tile]] = {
    16: _Tile(margin=0.0, corner=3.5, frame=1.0, inner_corner=2.5),
    20: _Tile(margin=0.0, corner=4.5, frame=1.0, inner_corner=3.5),
    24: _Tile(margin=1.0, corner=5.0, frame=2.0, inner_corner=3.5),
}


def _tile_for(size: int) -> _Tile:
    unit = size / _UNITS
    # 32-unit design: half a unit of margin, corner 7 (22 %), a 3-unit frame like the concept.
    return _TILES.get(size, _Tile(margin=0.5 * unit, corner=7.0 * unit, frame=3.0 * unit, inner_corner=5.0 * unit))


def brand_image(size: int) -> QImage:
    """The app icon at `size` px: the framed tile with the white mark and the coral head.

    Premultiplied ARGB32; needs no QGuiApplication, so `tools/gen_icon.py` uses it too.
    """
    tile = _tile_for(size)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        outer = QRectF(tile.margin, tile.margin, size - 2 * tile.margin, size - 2 * tile.margin)
        frame = QLinearGradient(outer.topRight(), outer.bottomLeft())
        frame.setColorAt(0.0, OUTER_TOP_RIGHT)
        frame.setColorAt(1.0, OUTER_BOTTOM_LEFT)
        painter.setBrush(QBrush(frame))
        painter.drawRoundedRect(outer, tile.corner, tile.corner)
        inner = outer.adjusted(tile.frame, tile.frame, -tile.frame, -tile.frame)
        fill = QLinearGradient(inner.topLeft(), inner.bottomLeft())
        fill.setColorAt(0.0, INNER_TOP)
        fill.setColorAt(1.0, INNER_BOTTOM)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(inner, tile.inner_corner, tile.inner_corner)
        # The mark fills about 85 % of the inner square's width, like the concept.
        unit = size / _UNITS
        pad = inner.width() * 0.07
        box = inner.adjusted(pad, pad, -pad, -pad)
        mark = _PIXEL_MARKS.get(("app", size)) or _place_mark(
            QRectF(box.left() / unit, box.top() / unit, box.width() / unit, box.height() / unit), size
        )
        _paint_mark(painter, mark, _WHITE, CORAL)
    finally:
        painter.end()
    return image


# ---------------------------------------------------------------------- badges


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
