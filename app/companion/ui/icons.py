"""Icons drawn with QPainter: the VoiceMate mark, the tray glyph per `TrayState`, the app icon.

The mark (docs/brand.md): one thick white sound-wave stroke that draws a capital M
(your voice becomes the companion, the M of Mate) and a coral dot above the middle
valley of the M (the companion listening). It sits on an indigo-to-violet rounded tile.

The tray reuses the mark and tells the state with the dot, the "status light", the
way the Windows microphone-in-use indicator does: a glance at the taskbar is enough.
States differ by SHAPE (dot, ring, open ring, ellipsis, sparkle, bubble, check, record
symbol, corner triangle or disc) and their color only reinforces it, so they stay
distinguishable for color-blind users and in high contrast.

Every size is painted on its own (no scaling of a big bitmap): 16, 20 and 24 px have their
own pixel-grid designs with straight edges on pixel boundaries, the other sizes use the
smooth 32-unit geometry. `tools/gen_icon.py` writes the packaged icon from `brand_image`,
so the exe, the windows and the tray all show the same mark.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
    QPolygonF,
)

import app.companion
from app.companion.contract import TrayState

Signal = Literal[
    "off",
    "ring",
    "open_ring",
    "dot",
    "record",
    "ellipsis",
    "sparkle",
    "bubble",
    "check",
    "triangle",
    "cross",
]

# Shipped by the packaging stream next to the package (the exe uses the same .ico).
ASSET_ICON: Final = Path(app.companion.__file__).parent / "assets" / "voicemate.ico"
TRAY_SIZES: Final = (16, 20, 24, 32, 40, 48, 64)
APP_ICON_SIZES: Final = (16, 20, 24, 32, 40, 48, 64, 128, 256)

STATE_SIGNALS: Final[dict[TrayState, Signal]] = {
    "stopped": "off",  # grey tile, no light
    "starting": "ring",  # hollow ring: the light is not on yet
    "restarting": "open_ring",  # a ring with a gap: going round again
    "idle": "dot",  # the brand coral dot: listening for the hotkey
    "recording": "record",  # big red dot in a white ring
    "transcribing": "ellipsis",  # three amber dots
    "thinking": "sparkle",  # cyan four-point sparkle: Claude is thinking
    "speaking": "bubble",  # cyan speech bubble: Claude's answer is being read
    "ready": "check",  # mint check: copied
    "warning": "triangle",  # amber triangle with "!" in the corner, no light
    "error": "cross",  # red disc with a white X in the corner, no light
}

TILE_TOP_LEFT: Final = QColor("#4338CA")
TILE_BOTTOM_RIGHT: Final = QColor("#7C3AED")
CORAL: Final = QColor("#FB7185")
_TILE_OFF_TOP_LEFT: Final = QColor("#3F3F46")
_TILE_OFF_BOTTOM_RIGHT: Final = QColor("#52525B")
_WAVE_OFF: Final = QColor("#D4D4D8")
_RED: Final = QColor("#EF4444")
_AMBER: Final = QColor("#F59E0B")
_CYAN: Final = QColor("#22D3EE")
_MINT: Final = QColor("#34D399")
_RING: Final = QColor("#E5E7EB")
_WHITE: Final = QColor("#FFFFFF")
_INK: Final = QColor("#1B1B1B")

Point = tuple[float, float]


@dataclass(frozen=True)
class _Design:
    """The mark on a `grid` x `grid` canvas, in pixels of that canvas (y grows downwards)."""

    grid: int
    margin: float  # transparent border around the tile
    corner: float  # tile corner radius
    stroke: float  # width of the M wave
    wave: tuple[Point, Point, Point, Point, Point]  # start, peak, valley, peak, end
    light: Point  # centre of the status light
    dot: float  # radius of the brand dot
    record: tuple[float, float]  # radius of the red dot, outer radius of its white ring
    ellipsis: tuple[float, float, float]  # y, spacing, radius of the three dots
    sparkle: tuple[float, float]  # outer and waist radius of the four-point sparkle
    bubble: tuple[float, float, float, float]  # centre y, width, height, corner radius of the speech bubble
    check: tuple[Point, Point, Point]  # the check mark's polyline
    ring: tuple[float, float]  # outer radius and width of the starting rings
    badge: tuple[float, float, float]  # centre x, centre y and radius of the corner badge
    gap: float  # transparent ring cut around a corner badge
    line: float  # width of the thin symbols (check, X, "!")


# 16 px: a 2 px wave whose bounding box (2..14 x 6..14) sits on pixel boundaries,
# symmetric about x = 8; a 4 px light centred above the valley. No margin: at 16 px
# half a pixel of margin would only blur the tile's edge.
_DESIGN_16: Final = _Design(
    grid=16,
    margin=0.0,
    corner=3.5,
    stroke=2.0,
    wave=((3.0, 13.0), (5.5, 7.0), (8.0, 11.0), (10.5, 7.0), (13.0, 13.0)),
    light=(8.0, 4.0),
    dot=2.0,
    record=(2.0, 3.0),
    ellipsis=(3.5, 3.0, 1.0),
    sparkle=(4.0, 1.6),
    bubble=(3.75, 7.0, 4.5, 1.6),
    check=((5.0, 3.75), (7.0, 5.75), (11.0, 1.75)),
    ring=(2.5, 1.1),
    badge=(12.0, 12.0, 4.0),
    gap=1.0,
    line=1.4,
)

# 20 px (the tray at 125 %): a 2 px wave on whole pixels (3..17 x 7..17), symmetric about
# x = 10; no margin, like 16 px.
_DESIGN_20: Final = _Design(
    grid=20,
    margin=0.0,
    corner=4.5,
    stroke=2.0,
    wave=((4.0, 16.0), (7.0, 8.0), (10.0, 13.0), (13.0, 8.0), (16.0, 16.0)),
    light=(10.0, 5.0),
    dot=2.0,
    record=(2.25, 3.5),
    ellipsis=(4.5, 3.5, 1.15),
    sparkle=(4.5, 1.8),
    bubble=(4.5, 8.0, 5.0, 1.8),
    check=((6.25, 4.5), (8.75, 7.0), (13.75, 2.0)),
    ring=(2.75, 1.25),
    badge=(15.0, 15.0, 5.0),
    gap=1.0,
    line=1.6,
)

# 24 px: a 3 px wave with its centre line on half pixels, so its outer edges are whole
# pixels (3..21 x 9..20); 1 px margin.
_DESIGN_24: Final = _Design(
    grid=24,
    margin=1.0,
    corner=5.0,
    stroke=3.0,
    wave=((4.5, 18.5), (8.5, 10.5), (12.0, 16.5), (15.5, 10.5), (19.5, 18.5)),
    light=(12.0, 5.5),
    dot=2.5,
    record=(2.75, 4.0),
    ellipsis=(5.5, 4.0, 1.4),
    sparkle=(5.25, 2.1),
    bubble=(5.5, 9.0, 6.0, 2.2),
    check=((7.75, 5.25), (10.75, 8.25), (16.25, 2.75)),
    ring=(3.25, 1.5),
    badge=(18.0, 18.0, 5.5),
    gap=1.25,
    line=2.0,
)

# Every other size: the smooth geometry on a 32-unit grid (exact at 32 and 64 px).
_DESIGN_SMOOTH: Final = _Design(
    grid=32,
    margin=1.0,
    corner=7.0,
    stroke=4.0,
    wave=((6.0, 22.5), (10.75, 12.5), (16.0, 20.0), (21.25, 12.5), (26.0, 22.5)),
    light=(16.0, 9.5),
    dot=2.6,
    record=(3.4, 5.0),
    ellipsis=(7.5, 5.0, 1.7),
    sparkle=(6.25, 2.5),
    bubble=(6.75, 10.0, 7.0, 2.5),
    check=((10.5, 7.0), (14.25, 10.75), (21.5, 3.5)),
    ring=(3.75, 1.75),
    badge=(24.0, 24.0, 7.0),
    gap=1.5,
    line=2.4,
)


def _design_for(pixels: int) -> _Design:
    return {16: _DESIGN_16, 20: _DESIGN_20, 24: _DESIGN_24}.get(pixels, _DESIGN_SMOOTH)


def tray_icon(state: TrayState) -> QIcon:
    """The tray glyph of `state` at every tray size. The tile carries its own colors, so
    one glyph reads on dark and light taskbars alike: there are no theme variants."""
    icon = QIcon()
    for size in TRAY_SIZES:
        icon.addPixmap(tray_pixmap(state, size))
    return icon


def tray_pixmap(state: TrayState, size: int) -> QPixmap:
    return QPixmap.fromImage(brand_image(size, state))


def state_pixmap(state: TrayState, size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """The tray glyph of `state` for a window (the status window's header), painted for
    `device_pixel_ratio`."""
    pixels = max(int(round(size * device_pixel_ratio)), 1)
    pixmap = QPixmap.fromImage(brand_image(pixels, state))
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def app_icon() -> QIcon:
    """Window / taskbar icon: the packaged `assets/voicemate.ico` (the same icon as the exe
    and the Start menu shortcut), or the mark drawn here when the assets are missing."""
    if ASSET_ICON.is_file():
        packaged = QIcon(str(ASSET_ICON))
        if not packaged.isNull():
            return packaged
    return drawn_app_icon()


def drawn_app_icon() -> QIcon:
    """The mark (tile, M wave and coral dot), painted at every size."""
    icon = QIcon()
    for size in APP_ICON_SIZES:
        icon.addPixmap(app_pixmap(size))
    return icon


def app_pixmap(size: int) -> QPixmap:
    return QPixmap.fromImage(brand_image(size))


def brand_image(size: int, state: TrayState = "idle") -> QImage:
    """The mark at `size` px with the status light of `state` (`idle` = the app icon).

    Premultiplied ARGB32; needs no QGuiApplication, so `tools/gen_icon.py` uses it too.
    """
    design = _design_for(size)
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / design.grid, size / design.grid)
        _paint(painter, design, STATE_SIGNALS[state])
    finally:
        painter.end()
    return image


def _paint(painter: QPainter, design: _Design, signal: Signal) -> None:
    off = signal == "off"
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_tile_brush(design, off))
    painter.drawPath(_tile_path(design))
    _paint_wave(painter, design, _WAVE_OFF if off else _WHITE)
    if signal in ("triangle", "cross"):
        _clear(painter, _badge_outline(design, signal), design.gap)
    _SIGNAL_PAINTERS[signal](painter, design)


def _tile_path(design: _Design) -> QPainterPath:
    side = design.grid - 2 * design.margin
    path = QPainterPath()
    path.addRoundedRect(QRectF(design.margin, design.margin, side, side), design.corner, design.corner)
    return path


def _tile_brush(design: _Design, off: bool = False) -> QBrush:
    start, end = design.margin, design.grid - design.margin
    gradient = QLinearGradient(start, start, end, end)
    gradient.setColorAt(0.0, _TILE_OFF_TOP_LEFT if off else TILE_TOP_LEFT)
    gradient.setColorAt(1.0, _TILE_OFF_BOTTOM_RIGHT if off else TILE_BOTTOM_RIGHT)
    return QBrush(gradient)


def _round_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _polyline(points: tuple[Point, ...]) -> QPainterPath:
    path = QPainterPath(QPointF(*points[0]))
    for point in points[1:]:
        path.lineTo(QPointF(*point))
    return path


def _paint_wave(painter: QPainter, design: _Design, color: QColor) -> None:
    painter.save()
    painter.setPen(_round_pen(color, design.stroke))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_polyline(design.wave))
    painter.restore()


def _grown(path: QPainterPath, by: float) -> QPainterPath:
    stroker = QPainterPathStroker()
    stroker.setWidth(2 * by)
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return path.united(stroker.createStroke(path)).simplified()


def _clear(painter: QPainter, outline: QPainterPath, gap: float) -> None:
    """Cut `outline` plus a `gap` ring out of the icon, down to transparency."""
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0))
    painter.drawPath(_grown(outline, gap))
    painter.restore()


def _disc(painter: QPainter, center: QPointF, radius: float, color: QColor | QBrush) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(center, radius, radius)


def _light(design: _Design) -> QPointF:
    return QPointF(*design.light)


def _paint_off(painter: QPainter, design: _Design) -> None:
    del painter, design  # the grey tile and wave say it: no light


def _paint_dot(painter: QPainter, design: _Design) -> None:
    _disc(painter, _light(design), design.dot, CORAL)


def _paint_record(painter: QPainter, design: _Design) -> None:
    inner, outer = design.record
    center = _light(design)
    # The ring would merge with the white wave where they touch: clear the tile around it.
    _disc(painter, center, outer + design.stroke / 4, _tile_brush(design))
    _disc(painter, center, outer, _WHITE)
    _disc(painter, center, inner, _RED)


def _paint_ellipsis(painter: QPainter, design: _Design) -> None:
    y, spacing, radius = design.ellipsis
    x = design.light[0]
    for step in (-1, 0, 1):
        _disc(painter, QPointF(x + step * spacing, y), radius, _AMBER)


def _paint_sparkle(painter: QPainter, design: _Design) -> None:
    outer, waist = design.sparkle
    cx, cy = design.light
    points = [
        QPointF(cx + radius * math.sin(math.radians(45.0 * step)), cy - radius * math.cos(math.radians(45.0 * step)))
        for step, radius in ((step, outer if step % 2 == 0 else waist) for step in range(8))
    ]
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_CYAN)
    painter.drawPolygon(QPolygonF(points))


def _paint_bubble(painter: QPainter, design: _Design) -> None:
    cy, width, height, corner = design.bubble
    cx = design.light[0]
    body = QRectF(cx - width / 2, cy - height / 2, width, height)
    path = QPainterPath()
    path.addRoundedRect(body, corner, corner)
    # The tail points down into the valley of the M: the companion is the one talking.
    tail = height * 0.5
    # A union, not a second subpath: with the odd-even fill the overlap would be a hole.
    tail_path = QPainterPath()
    tail_path.addPolygon(
        QPolygonF(
            [
                QPointF(cx - tail * 0.7, body.bottom() - 0.5),
                QPointF(cx - tail * 0.5, body.bottom() + tail),
                QPointF(cx + tail * 0.5, body.bottom() - 0.5),
            ]
        )
    )
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_CYAN)
    painter.drawPath(path.united(tail_path))


def _paint_check(painter: QPainter, design: _Design) -> None:
    painter.setPen(_round_pen(_MINT, design.line * 1.15))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_polyline(design.check))


def _ring_rect(design: _Design) -> QRectF:
    outer, width = design.ring
    radius = outer - width / 2  # of the stroke's centre line
    cx, cy = design.light
    return QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius)


def _knock_out_ring(painter: QPainter, design: _Design) -> None:
    """A light-grey ring would merge with the white wave where they touch: clear the tile around it."""
    _disc(painter, _light(design), design.ring[0] + design.stroke / 4, _tile_brush(design))


def _paint_ring(painter: QPainter, design: _Design) -> None:
    _knock_out_ring(painter, design)
    painter.setPen(_round_pen(_RING, design.ring[1]))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(_ring_rect(design))


def _paint_open_ring(painter: QPainter, design: _Design) -> None:
    _knock_out_ring(painter, design)
    painter.setPen(_round_pen(_RING, design.ring[1]))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # From 12 o'clock counter-clockwise round to 5 o'clock: a wide gap on the right.
    painter.drawArc(_ring_rect(design), 90 * 16, 210 * 16)
    # An arrow head at 12 o'clock pointing into the gap: going round again.
    width = design.ring[1]
    top = _ring_rect(design).top()
    cx = design.light[0]
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_RING)
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(cx + 1.4 * width, top),
                QPointF(cx - 0.2 * width, top - 1.2 * width),
                QPointF(cx - 0.2 * width, top + 1.2 * width),
            ]
        )
    )


def _badge_outline(design: _Design, signal: Signal) -> QPainterPath:
    cx, cy, radius = design.badge
    path = QPainterPath()
    if signal == "triangle":
        bottom = cy + radius
        path.addPolygon(
            QPolygonF(
                [
                    QPointF(cx, cy - radius * 0.95),
                    QPointF(cx + radius * 1.05, bottom),
                    QPointF(cx - radius * 1.05, bottom),
                    QPointF(cx, cy - radius * 0.95),
                ]
            )
        )
    else:
        path.addEllipse(QPointF(cx, cy), radius, radius)
    return path


def _paint_triangle(painter: QPainter, design: _Design) -> None:
    cx, cy, radius = design.badge
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_AMBER)
    painter.drawPath(_badge_outline(design, "triangle"))
    painter.setPen(_round_pen(_INK, design.line))
    painter.drawLine(QPointF(cx, cy - radius * 0.3), QPointF(cx, cy + radius * 0.25))
    _disc(painter, QPointF(cx, cy + radius * 0.68), design.line * 0.55, _INK)


def _paint_cross(painter: QPainter, design: _Design) -> None:
    cx, cy, radius = design.badge
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_RED)
    painter.drawPath(_badge_outline(design, "cross"))
    arm = radius * 0.45
    painter.setPen(_round_pen(_WHITE, design.line))
    painter.drawLine(QPointF(cx - arm, cy - arm), QPointF(cx + arm, cy + arm))
    painter.drawLine(QPointF(cx + arm, cy - arm), QPointF(cx - arm, cy + arm))


_SIGNAL_PAINTERS: Final[dict[Signal, Callable[[QPainter, _Design], None]]] = {
    "off": _paint_off,
    "ring": _paint_ring,
    "open_ring": _paint_open_ring,
    "dot": _paint_dot,
    "record": _paint_record,
    "ellipsis": _paint_ellipsis,
    "sparkle": _paint_sparkle,
    "bubble": _paint_bubble,
    "check": _paint_check,
    "triangle": _paint_triangle,
    "cross": _paint_cross,
}
