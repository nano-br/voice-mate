"""Icons drawn with QPainter: the VoiceMate mark, the tray glyph per `TrayState`, the app icon.

The mark (docs/brand.md) is a person with raised arms: one thick sound-wave stroke whose
outer ends are the arms and whose deep middle V is the body (your voice), with a coral
dot above it as the head (the companion listening). The dot is always coral.

Tray icons follow the Windows 11 style: the mark alone in the taskbar's foreground color
(no tile), the same size and place in every state, plus a small colored badge in the
bottom-right corner that tells the state. Badges differ by SHAPE (disc, ring, open ring,
octagon, triangle, rounded square, speech bubble, hourglass, loudspeaker) and by their
symbol, never only by color. The app icon (exe, windows, installer) puts the mark on a
framed tile: a blue-to-magenta outer square around a deep-indigo inner square.

Every size is painted on its own (no scaling of a big bitmap): below 48 px the mark's
stroke width is a whole number of pixels and its caps, joins and head land on the pixel
grid; the marks at 16, 20 and 24 px (tray and app icon) are hand-tuned, the badges at 20
and 24 px have their own geometry and at 16 px are pixel art, so 16 px stays crisp at
100 % and the bigger variants serve high DPI. `tools/gen_icon.py` writes the packaged
icon from `brand_image`.
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
    "ring",
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
    "starting": "ring",
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

_TRAY_BOX: Final = QRectF(0.5, 0.0, 31.0, 28.0)  # the mark in the tray, the same in every state
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


def _place_mark(box: QRectF, size: int) -> _PixelMark:
    """Fit the mark into `box` (32-unit grid) of a `size` px icon, centred. Small sizes
    snap to the pixel grid."""
    bounds = _MARK.bounds()
    unit = size / _UNITS
    scale = min(box.width() / bounds.width(), box.height() / bounds.height()) * unit
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


Layout = Literal["app", "tray"]


def _hand(wave: tuple[Point, ...], stroke: float, head: Point, radius: float) -> _PixelMark:
    return _PixelMark(tuple(QPointF(*point) for point in wave), stroke, QPointF(*head), radius)


# Hand-tuned on their own pixel grids where the automatic snap loses the figure: a head
# at least as big as the stroke, raised above the shoulders with a 1 px gap, the body's
# lowest point well below the arms. In the tray the V is narrow and the arms end high
# enough that the corner badge's cut-out trims at most the tip of the right arm, never
# the body; 24 px keeps a 3 px stroke with its axis on a pixel centre and a free edge.
_PIXEL_MARKS: Final[dict[tuple[Layout, int], _PixelMark]] = {
    ("app", 16): _hand(((3, 10), (5, 6), (8, 12), (11, 6), (13, 10)), 2, (8, 3), 1),
    ("app", 20): _hand(((3, 13), (6, 8), (10, 16), (14, 8), (17, 13)), 2, (10, 4), 2),
    ("app", 24): _hand(((5, 15), (8, 10), (12, 18), (16, 10), (19, 15)), 2, (12, 6), 2),
    ("tray", 16): _hand(((1, 10), (5, 6), (8, 13), (11, 6), (15, 10)), 2, (8, 3), 2),
    ("tray", 20): _hand(((2, 12), (7, 7), (10, 17), (13, 7), (18, 12)), 2, (10, 4), 2),
    ("tray", 24): _hand(((2.5, 13.5), (6.5, 6.5), (11.5, 18.5), (16.5, 6.5), (20.5, 13.5)), 3, (11.5, 3.5), 2.5),
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
    """The tray glyph of `state` at `size` px: the mark without a tile, the same in every
    state (only `stopped` dims it), plus the state's small badge in the bottom-right corner."""
    badge = STATE_BADGES[state]
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mark = _PIXEL_MARKS.get(("tray", size)) or _place_mark(_TRAY_BOX, size)
        head = glyph if state == "stopped" else CORAL  # stopped: the whole glyph is dimmed
        _paint_mark(painter, mark, glyph, head)
        art = _BADGE_ART.get((size, badge))
        if badge != "none" and art is None:
            spec = _badge_spec(size)
            _clear(painter, spec)
            painter.save()
            painter.setClipPath(_disc(spec, spec.radius))  # every badge stays inside its disc
            _BADGE_PAINTERS[badge](painter, spec)
            painter.restore()
    finally:
        painter.end()
    if art is not None:
        _put_badge_art(image, art, _ART_ORIGINS[size])
    return image


# At 16, 20 and 24 px the badges are pixel art (6 x 6, 7 x 7 and 8 x 8) in the corner:
# vector shapes that small only blur. "." keeps the glyph's pixel (the right arm's tip may
# show through a corner); every other cell replaces it. B = the badge's color, W = white,
# K = dark ink. The art starts right of the body's V and its anti-aliased edge (column 10
# at 16 px, 13 at 20, 16 at 24; at 16 px the art's top-left cell stays empty), so it never
# touches the body.
_ART_ORIGINS: Final[dict[int, int]] = {16: 10, 20: 13, 24: 16}
_BADGE_ART: Final[dict[tuple[int, Badge], tuple[QColor, tuple[str, ...]]]] = {
    (16, "octagon"): (_GREY, (".BBBB.", "BBBBBB", "BWWWWB", "BWWWWB", "BBBBBB", ".BBBB.")),
    (16, "ring"): (_GREY, (".BBBB.", "BB..BB", "B....B", "B....B", "BB..BB", ".BBBB.")),
    (16, "arrows"): (_GREY, (".BBBBB", "BB..BB", "B..BBB", "B.....", "BB..B.", ".BBB..")),
    (16, "record"): (_RED, (".BBBB.", "BBBBBB", "BBBBBB", "BBBBBB", "BBBBBB", ".BBBB.")),
    (16, "hourglass"): (_AMBER, (".BBBBB", "..BBB.", "...B..", "...B..", "..BBB.", ".BBBBB")),
    (16, "bubble"): (_VIOLET, (".BBBB.", "BBBBBB", "BBBBBB", ".BBBB.", ".BB...", ".B....")),
    (16, "speaker"): (_VIOLET, ("...B..", "..BB.B", "BBBB.B", "BBBB.B", "..BB.B", "...B..")),
    (16, "check"): (_GREEN, (".BBBB.", "BBBBWB", "BBBBWB", "BWBWBB", "BBWBBB", ".BBBB.")),
    (16, "triangle"): (_YELLOW, ("...B..", "..BKB.", "..BKB.", ".BBBBB", ".BBKBB", ".BBBBB")),
    (16, "cross"): (_RED, (".BBBB.", "BWBBWB", "BBWWBB", "BBWWBB", "BWBBWB", ".BBBB.")),
    (20, "octagon"): (_GREY, ("..BBB..", ".BBBBB.", "BBBBBBB", "BWWWWWB", "BBBBBBB", ".BBBBB.", "..BBB..")),
    (20, "ring"): (_GREY, ("..BBB..", ".BB.BB.", "BB...BB", "B.....B", "BB...BB", ".BB.BB.", "..BBB..")),
    (20, "arrows"): (_GREY, ("..BBBB.", ".BB.BBB", "BB...B.", "B......", "BB.....", ".BB....", "..BBB..")),
    (20, "record"): (_RED, ("..BBB..", ".BBBBB.", "BBBBBBB", "BBBBBBB", "BBBBBBB", ".BBBBB.", "..BBB..")),
    (20, "hourglass"): (_AMBER, ("BBBBBBB", ".BBBBB.", "..BBB..", "...B...", "..BBB..", ".BBBBB.", "BBBBBBB")),
    (20, "bubble"): (_VIOLET, (".BBBBB.", "BBBBBBB", "BWBWBWB", "BBBBBBB", ".BBBBB.", ".BB....", ".B.....")),
    (20, "speaker"): (_VIOLET, ("...B...", "..BB.B.", "BBBB..B", "BBBB..B", "BBBB..B", "..BB.B.", "...B...")),
    (20, "check"): (_GREEN, ("..BBB..", ".BBBBB.", "BBBBBWB", "BBBBWWB", "BWBWWBB", ".BWWBB.", "..BBB..")),
    (20, "triangle"): (_YELLOW, ("...B...", "..BBB..", "..BKB..", ".BBKBB.", ".BBBBB.", "BBBKBBB", "BBBBBBB")),
    (20, "cross"): (_RED, (".BBBBB.", "BWBBBWB", "BBWBWBB", "BBBWBBB", "BBWBWBB", "BWBBBWB", ".BBBBB.")),
    (24, "octagon"): (
        _GREY,
        ("..BBBB..", ".BBBBBB.", "BBBBBBBB", "BWWWWWWB", "BWWWWWWB", "BBBBBBBB", ".BBBBBB.", "..BBBB.."),
    ),
    (24, "ring"): (
        _GREY,
        ("..BBBB..", ".BB..BB.", "BB....BB", "B......B", "B......B", "BB....BB", ".BB..BB.", "..BBBB.."),
    ),
    (24, "arrows"): (
        _GREY,
        ("..BBBBB.", ".BB...BB", "BB..BBBB", "B....BB.", "B.......", "BB......", ".BB.....", "..BBBB.."),
    ),
    (24, "record"): (
        _RED,
        ("..BBBB..", ".BBBBBB.", "BBBBBBBB", "BBBBBBBB", "BBBBBBBB", "BBBBBBBB", ".BBBBBB.", "..BBBB.."),
    ),
    (24, "hourglass"): (
        _AMBER,
        ("BBBBBBBB", ".BBBBBB.", "..BBBB..", "...BB...", "...BB...", "..BBBB..", ".BBBBBB.", "BBBBBBBB"),
    ),
    (24, "bubble"): (
        _VIOLET,
        (".BBBBBB.", "BBBBBBBB", "BBBBBBBB", "BWBWBWBB", "BBBBBBBB", ".BBBBBB.", ".BBB....", ".B......"),
    ),
    (24, "speaker"): (
        _VIOLET,
        ("....B...", "...BB.B.", "BBBBB..B", "BBBBB..B", "BBBBB..B", "BBBBB..B", "...BB.B.", "....B..."),
    ),
    (24, "check"): (
        _GREEN,
        ("..BBBB..", ".BBBBBB.", "BBBBBBWB", "BBBBBWWB", "BWBBWWBB", "BWWWWBBB", ".BWWBBB.", "..BBBB.."),
    ),
    (24, "triangle"): (
        _YELLOW,
        ("...BB...", "...BB...", "..BKKB..", "..BKKB..", ".BBKKBB.", ".BBBBBB.", "BBBKKBBB", "BBBBBBBB"),
    ),
    (24, "cross"): (
        _RED,
        (".BBBBBB.", "BWBBBBWB", "BBWBBWBB", "BBBWWBBB", "BBBWWBBB", "BBWBBWBB", "BWBBBBWB", ".BBBBBB."),
    ),
}


def _put_badge_art(image: QImage, art: tuple[QColor, tuple[str, ...]], origin: int) -> None:
    color, rows = art
    palette = {"B": color, "W": _WHITE, "K": _INK}
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell != ".":
                image.setPixelColor(origin + x, origin + y, palette[cell])


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


@dataclass(frozen=True)
class _BadgeSpec:
    """The corner badge in device pixels: centre, radius, the transparent ring cut around
    it into the mark, and the width of the symbols' lines."""

    center: QPointF
    radius: float
    gap: float
    line: float


# Small, in the bottom-right corner: the cut-out (radius + gap) never reaches the body's V.
# At 16, 20 and 24 px the pixel art (_BADGE_ART) replaces the vector badge; these discs are
# the area it covers, for the tests.
_BADGES: Final[dict[int, _BadgeSpec]] = {
    16: _BadgeSpec(QPointF(13.0, 13.0), 3.0, 0.0, 1.0),
    20: _BadgeSpec(QPointF(16.5, 16.5), 3.5, 0.0, 1.2),
    24: _BadgeSpec(QPointF(20.0, 20.0), 4.0, 0.0, 1.4),
}


def _badge_spec(size: int) -> _BadgeSpec:
    unit = size / _UNITS
    # 32-unit design: radius 5.5 centred at (26.5, 26.5), a 0.75-unit ring: low and right
    # enough that the cut-out trims at most the tip of the right arm, never the body.
    return _BADGES.get(size, _BadgeSpec(QPointF(26.5 * unit, 26.5 * unit), 5.5 * unit, 0.75 * unit, 1.8 * unit))


def _octagon(spec: _BadgeSpec) -> QPolygonF:
    reach = spec.radius  # corners on the disc's edge
    c = spec.center
    return QPolygonF(
        [
            QPointF(c.x() + reach * math.cos(angle), c.y() + reach * math.sin(angle))
            for angle in (math.radians(22.5 + 45.0 * step) for step in range(8))
        ]
    )


def _triangle(spec: _BadgeSpec) -> QPolygonF:
    c, r = spec.center, spec.radius
    return QPolygonF(
        [
            QPointF(c.x(), c.y() - r),
            QPointF(c.x() + 0.95 * r, c.y() + 0.62 * r),
            QPointF(c.x() - 0.95 * r, c.y() + 0.62 * r),
        ]
    )


def _bubble_body(spec: _BadgeSpec) -> QRectF:
    c, r = spec.center, spec.radius
    return QRectF(c.x() - 0.88 * r, c.y() - 0.78 * r, 1.76 * r, 1.15 * r)


def _badge_outline(badge: Badge, spec: _BadgeSpec) -> QPainterPath:
    c, r = spec.center, spec.radius
    path = QPainterPath()
    if badge == "octagon":
        path.addPolygon(_octagon(spec))
        path.closeSubpath()
    elif badge == "triangle":
        path.addPolygon(_triangle(spec))
        path.closeSubpath()
    elif badge == "hourglass":
        path.addPolygon(_hourglass(spec))
        path.closeSubpath()
    elif badge == "speaker":
        path.addPolygon(_speaker(spec))
        path.closeSubpath()
        c, r = spec.center, spec.radius
        path.addRect(QRectF(c.x() + 0.2 * r, c.y() - 0.75 * r, 0.75 * r, 1.5 * r))  # the sound arc's room
    elif badge == "cross":
        side = 1.5 * r
        path.addRoundedRect(QRectF(c.x() - side / 2, c.y() - side / 2, side, side), 0.25 * r, 0.25 * r)
    elif badge == "bubble":
        body = _bubble_body(spec)
        path.addRoundedRect(body, 0.38 * r, 0.38 * r)
        tail = QPainterPath()
        tail.addPolygon(
            QPolygonF(
                [
                    QPointF(c.x() - 0.6 * r, body.bottom() - 0.2 * r),
                    QPointF(c.x() - 0.55 * r, c.y() + 0.82 * r),
                    QPointF(c.x() + 0.1 * r, body.bottom() - 0.2 * r),
                ]
            )
        )
        path = path.united(tail)
    else:
        path.addEllipse(c, r, r)
    return path.intersected(_disc(spec, r))


def _disc(spec: _BadgeSpec, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(spec.center, radius, radius)
    return path


def _clear(painter: QPainter, spec: _BadgeSpec) -> None:
    """Cut the badge's disc plus its `gap` ring out of the glyph, down to transparency.
    A disc whatever the badge's shape: its reach towards the figure is known."""
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0))
    painter.drawPath(_disc(spec, spec.radius + spec.gap))
    painter.restore()


def _fill(painter: QPainter, badge: Badge, spec: _BadgeSpec, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPath(_badge_outline(badge, spec))


def _line_pen(color: QColor, width: float, cap: Qt.PenCapStyle = Qt.PenCapStyle.RoundCap) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _paint_octagon(painter: QPainter, spec: _BadgeSpec) -> None:
    _fill(painter, "octagon", spec, _GREY)
    c, r = spec.center, spec.radius
    painter.setPen(_line_pen(_WHITE, 1.6 * spec.line, Qt.PenCapStyle.FlatCap))
    painter.drawLine(QPointF(c.x() - 0.6 * r, c.y()), QPointF(c.x() + 0.6 * r, c.y()))


def _ring_width(spec: _BadgeSpec) -> float:
    return max(spec.line, 0.38 * spec.radius)  # thin enough to leave a clear hole


def _ring_rect(spec: _BadgeSpec) -> QRectF:
    inset = _ring_width(spec) / 2
    radius = spec.radius - inset
    c = spec.center
    return QRectF(c.x() - radius, c.y() - radius, 2 * radius, 2 * radius)


def _paint_ring(painter: QPainter, spec: _BadgeSpec) -> None:
    # Starting: a closed grey ring.
    painter.setPen(_line_pen(_GREY, _ring_width(spec)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(_ring_rect(spec))


def _paint_arrows(painter: QPainter, spec: _BadgeSpec) -> None:
    # Restarting: two thirds of a smaller ring, open on the left, and an arrow head at the
    # top pointing into the gap: a circular arrow, not the closed ring of "starting".
    c, r = spec.center, spec.radius
    width = _ring_width(spec)
    reach = 0.62 * r  # the arc's centre line; the arrow head ends at the disc's edge
    rect = QRectF(c.x() - reach, c.y() - reach, 2 * reach, 2 * reach)
    painter.setPen(_line_pen(_GREY, width, Qt.PenCapStyle.FlatCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(rect, 90 * 16, -230 * 16)  # clockwise from 12 o'clock to about 8 o'clock
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_GREY)
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(c.x() - 0.6 * r, c.y() - reach),
                QPointF(c.x() + 0.1 * r, c.y() - r),
                QPointF(c.x() + 0.1 * r, c.y() - reach + 0.38 * r),
            ]
        )
    )


def _paint_record(painter: QPainter, spec: _BadgeSpec) -> None:
    _fill(painter, "record", spec, _RED)


def _hourglass(spec: _BadgeSpec) -> QPolygonF:
    """An amber hourglass silhouette: two triangles meeting at the waist."""
    c, r = spec.center, spec.radius
    return QPolygonF(
        [
            QPointF(c.x() - 0.62 * r, c.y() - 0.78 * r),
            QPointF(c.x() + 0.62 * r, c.y() - 0.78 * r),
            QPointF(c.x() + 0.13 * r, c.y()),
            QPointF(c.x() + 0.62 * r, c.y() + 0.78 * r),
            QPointF(c.x() - 0.62 * r, c.y() + 0.78 * r),
            QPointF(c.x() - 0.13 * r, c.y()),
        ]
    )


def _paint_hourglass(painter: QPainter, spec: _BadgeSpec) -> None:
    _fill(painter, "hourglass", spec, _AMBER)


def _paint_bubble(painter: QPainter, spec: _BadgeSpec) -> None:
    _fill(painter, "bubble", spec, _VIOLET)
    if spec.radius < 5:  # too small for the dots: the bubble's silhouette says it
        return
    body = _bubble_body(spec)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_WHITE)
    for dx in (-0.45, 0.0, 0.45):
        painter.drawEllipse(
            QPointF(body.center().x() + dx * spec.radius, body.center().y()), 0.14 * spec.radius, 0.14 * spec.radius
        )


def _speaker(spec: _BadgeSpec) -> QPolygonF:
    """A loudspeaker silhouette: a small box and its cone, opening to the right."""
    c, r = spec.center, spec.radius
    return QPolygonF(
        [
            QPointF(c.x() - 0.95 * r, c.y() - 0.38 * r),
            QPointF(c.x() - 0.45 * r, c.y() - 0.38 * r),
            QPointF(c.x() + 0.2 * r, c.y() - 0.92 * r),
            QPointF(c.x() + 0.2 * r, c.y() + 0.92 * r),
            QPointF(c.x() - 0.45 * r, c.y() + 0.38 * r),
            QPointF(c.x() - 0.95 * r, c.y() + 0.38 * r),
        ]
    )


def _paint_speaker(painter: QPainter, spec: _BadgeSpec) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_VIOLET)
    painter.drawPolygon(_speaker(spec))
    c, r = spec.center, spec.radius
    painter.setPen(_line_pen(_VIOLET, max(spec.line, 0.22 * r), Qt.PenCapStyle.FlatCap))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    reach = 0.9 * r - max(spec.line, 0.22 * r) / 2  # the sound arc stays inside the badge
    painter.drawArc(QRectF(c.x() - reach, c.y() - reach, 2 * reach, 2 * reach), -50 * 16, 100 * 16)


def _paint_check(painter: QPainter, spec: _BadgeSpec) -> None:
    _fill(painter, "check", spec, _GREEN)
    c, r = spec.center, spec.radius
    painter.setPen(_line_pen(_WHITE, 1.2 * spec.line))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(c.x() - 0.5 * r, c.y() + 0.02 * r))
    path.lineTo(QPointF(c.x() - 0.12 * r, c.y() + 0.42 * r))
    path.lineTo(QPointF(c.x() + 0.52 * r, c.y() - 0.4 * r))
    painter.drawPath(path)


def _paint_triangle(painter: QPainter, spec: _BadgeSpec) -> None:
    _fill(painter, "triangle", spec, _YELLOW)
    c, r = spec.center, spec.radius
    painter.setPen(_line_pen(_INK, spec.line, Qt.PenCapStyle.FlatCap))
    painter.drawLine(QPointF(c.x(), c.y() - 0.42 * r), QPointF(c.x(), c.y() + 0.08 * r))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_INK)
    dot = spec.line / 2
    painter.drawRect(QRectF(c.x() - dot, c.y() + 0.24 * r, 2 * dot, 2 * dot))


def _paint_cross(painter: QPainter, spec: _BadgeSpec) -> None:
    _fill(painter, "cross", spec, _RED)
    c, r = spec.center, spec.radius
    arm = 0.46 * r
    painter.setPen(_line_pen(_WHITE, 1.3 * spec.line))
    painter.drawLine(QPointF(c.x() - arm, c.y() - arm), QPointF(c.x() + arm, c.y() + arm))
    painter.drawLine(QPointF(c.x() + arm, c.y() - arm), QPointF(c.x() - arm, c.y() + arm))


_BADGE_PAINTERS: Final[dict[Badge, Callable[[QPainter, _BadgeSpec], None]]] = {
    "octagon": _paint_octagon,
    "ring": _paint_ring,
    "arrows": _paint_arrows,
    "record": _paint_record,
    "hourglass": _paint_hourglass,
    "bubble": _paint_bubble,
    "speaker": _paint_speaker,
    "check": _paint_check,
    "triangle": _paint_triangle,
    "cross": _paint_cross,
}
