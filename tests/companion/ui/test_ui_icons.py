"""The VoiceMate mark, the tray glyphs and the app icon (app/companion/ui/icons.py, docs/brand.md)."""

from __future__ import annotations

import itertools
import math
import struct
from pathlib import Path
from typing import get_args

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.companion.contract import TrayState  # noqa: E402
from app.companion.ui import icons  # noqa: E402

ALL_STATES: tuple[TrayState, ...] = get_args(TrayState)
TONES: tuple[icons.GlyphTone, ...] = ("light", "dark")
_STRAIGHT = QImage.Format.Format_ARGB32


def _image(state: TrayState, tone: icons.GlyphTone, size: int) -> QImage:
    return icons.tray_pixmap(state, tone, size).toImage().convertToFormat(_STRAIGHT)


def _planes(state: TrayState, tone: icons.GlyphTone, size: int) -> list[tuple[int, int]]:
    """(lightness, alpha) per pixel: what is left of the icon without colors."""
    image = _image(state, tone, size)
    return [
        (image.pixelColor(x, y).lightness(), image.pixelColor(x, y).alpha()) for y in range(size) for x in range(size)
    ]


def _visible_difference(first: list[tuple[int, int]], second: list[tuple[int, int]]) -> int:
    """Pixels that differ clearly: a big step in lightness, or in coverage (the silhouette)."""
    return sum(
        1
        for (light_a, alpha_a), (light_b, alpha_b) in zip(first, second, strict=True)
        if abs(alpha_a - alpha_b) >= 128 or (min(alpha_a, alpha_b) >= 128 and abs(light_a - light_b) >= 64)
    )


def _silhouette(state: TrayState, size: int) -> list[bool]:
    image = _image(state, "light", size)
    return [image.pixelColor(x, y).alpha() >= 128 for y in range(size) for x in range(size)]


def _count(image: QImage, color: QColor) -> int:
    return sum(1 for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y) == color)


# The badge is small (6 px wide at 16 px) and the mark never changes: ask for a clear
# difference in at least 5 of the badge's pixels at 16 px, a few more as it grows.
_MIN_DIFFERENT_PIXELS = {16: 5, 20: 7, 24: 10, 32: 20}


@pytest.mark.parametrize("size", icons.TRAY_SIZES)
@pytest.mark.parametrize("tone", TONES)
@pytest.mark.parametrize("state", ALL_STATES)
def test_every_state_renders_at_every_tray_size(
    qapp: QApplication, state: TrayState, tone: icons.GlyphTone, size: int
) -> None:
    pixmap = icons.tray_pixmap(state, tone, size)
    assert (pixmap.width(), pixmap.height()) == (size, size)
    image = pixmap.toImage().convertToFormat(_STRAIGHT)
    # A glyph, not a tile: the top-right corner stays transparent and the glyph color is there.
    assert image.pixelColor(size - 1, 0).alpha() == 0
    assert _count(image, icons.glyph_color(tone, state)) > 0
    # The head is the same coral in every state; `stopped` dims the whole glyph.
    assert (_count(image, icons.CORAL) > 0) == (state != "stopped")


@pytest.mark.parametrize("size", icons.TRAY_SIZES)
@pytest.mark.parametrize("tone", TONES)
def test_every_state_draws_a_different_icon(qapp: QApplication, tone: icons.GlyphTone, size: int) -> None:
    images = {state: _image(state, tone, size) for state in ALL_STATES}
    for first, second in itertools.combinations(ALL_STATES, 2):
        assert images[first] != images[second], f"{first}/{second} at {size} px"


@pytest.mark.parametrize("size", [16, 20, 24, 32])
@pytest.mark.parametrize("tone", TONES)
def test_every_tray_state_is_distinguishable_without_color(
    qapp: QApplication, tone: icons.GlyphTone, size: int
) -> None:
    planes = {state: _planes(state, tone, size) for state in ALL_STATES}
    for first, second in itertools.combinations(ALL_STATES, 2):
        different = _visible_difference(planes[first], planes[second])
        assert different >= _MIN_DIFFERENT_PIXELS[size], f"{first}/{second}: {different} px at {size} px"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("stopped", "idle"),
        ("starting", "restarting"),
        ("recording", "starting"),
        ("warning", "error"),
        ("thinking", "speaking"),
    ],
)
@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_related_states_differ_by_badge_shape(
    qapp: QApplication, first: TrayState, second: TrayState, size: int
) -> None:
    assert _silhouette(first, size) != _silhouette(second, size)


def _mask(path: QPainterPath, size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#000000"))
    painter.drawPath(path)
    painter.end()
    return image


def test_badges_have_distinct_silhouettes() -> None:
    """Octagon, disc, triangle, rounded square, speech bubble, hourglass and loudspeaker."""
    spec = icons._badge_spec(64)
    badges: tuple[icons.Badge, ...] = ("octagon", "record", "triangle", "cross", "bubble", "hourglass", "speaker")
    masks = {badge: _mask(icons._badge_outline(badge, spec), 64) for badge in badges}
    for first, second in itertools.combinations(badges, 2):
        assert masks[first] != masks[second], f"{first}/{second}"


@pytest.mark.parametrize("size", icons.TRAY_SIZES)
@pytest.mark.parametrize("tone", TONES)
def test_the_mark_is_the_same_in_every_state(qapp: QApplication, tone: icons.GlyphTone, size: int) -> None:
    """Only the small corner badge changes (and `stopped` dims the glyph): the mark keeps
    its size and place, so the icon always looks the same at a glance."""
    spec = icons._badge_spec(size)
    reach = spec.radius * 1.5 + spec.gap + 1  # the badge's corners, its cut-out ring, anti-aliasing
    idle = _image("idle", tone, size)
    outside = [
        (x, y)
        for y in range(size)
        for x in range(size)
        if math.hypot(x + 0.5 - spec.center.x(), y + 0.5 - spec.center.y()) > reach
    ]
    for state in ALL_STATES:
        if state == "stopped":
            continue
        image = _image(state, tone, size)
        assert all(image.pixelColor(x, y) == idle.pixelColor(x, y) for x, y in outside), state
    # The badge stays small: under 42 % of the icon's width.
    assert 2 * spec.radius <= size * 0.42


@pytest.mark.parametrize("size", [16, 20, 24, 32, 40, 48, 64])
@pytest.mark.parametrize("tone", TONES)
def test_a_badge_never_cuts_into_the_body(qapp: QApplication, tone: icons.GlyphTone, size: int) -> None:
    """The cut-out may trim the right arm's tip, never the V (the figure's body)."""
    mark = icons._PIXEL_MARKS.get(("tray", size)) or icons._place_mark(icons._TRAY_BOX, size)
    body_right = mark.wave[3].x() - mark.stroke / 2  # the right arm starts past this column;
    # the column holding it is included, for the V's anti-aliased edge
    idle = _image("idle", tone, size)
    body = [(x, y) for y in range(size) for x in range(size) if x <= body_right and idle.pixelColor(x, y).alpha() > 0]
    for state in ALL_STATES:
        if state in ("idle", "stopped"):
            continue
        image = _image(state, tone, size)
        changed = [(x, y) for x, y in body if image.pixelColor(x, y) != idle.pixelColor(x, y)]
        assert not changed, f"{state} at {size} px: {changed}"


def test_tray_icon_has_every_size_and_follows_the_taskbar_tone(qapp: QApplication) -> None:
    icon = icons.tray_icon("recording", "light")
    available = {size.width() for size in icon.availableSizes()}
    assert set(icons.TRAY_SIZES) <= available
    light = icons.tray_pixmap("idle", "light", 16).toImage()
    dark = icons.tray_pixmap("idle", "dark", 16).toImage()
    assert light != dark


def test_state_pixmap_paints_for_the_device_pixel_ratio(qapp: QApplication) -> None:
    pixmap = icons.state_pixmap("recording", QColor("#202020"), 40, 1.5)
    assert (pixmap.width(), pixmap.height()) == (60, 60)
    assert pixmap.devicePixelRatio() == 1.5


@pytest.mark.parametrize("size", icons.APP_ICON_SIZES)
def test_app_icon_is_the_framed_tile_with_the_mark(size: int) -> None:
    image = icons.brand_image(size).convertToFormat(_STRAIGHT)
    centre_column = [image.pixelColor(size // 2, y) for y in range(size)]
    assert image.pixelColor(size // 2, size // 2).alpha() == 255  # a tile, not a glyph
    assert any(color == icons.CORAL for color in centre_column)  # the head, on the axis
    assert sum(1 for y in range(size) for x in range(size) if image.pixelColor(x, y) == QColor("#FFFFFF")) > 0


def test_app_icon_prefers_the_packaged_icon(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(icons, "ASSET_ICON", tmp_path / "missing.ico")
    assert not icons.app_icon().isNull()  # drawn fallback
    packaged = tmp_path / "voicemate.ico"
    icons.app_pixmap(48).save(str(packaged), "PNG")  # format detected by content
    monkeypatch.setattr(icons, "ASSET_ICON", packaged)
    assert icons.app_icon().availableSizes() == [icons.app_pixmap(48).size()]


def _pixels(image: QImage) -> list[tuple[int, int, int, int]]:
    image = image.convertToFormat(_STRAIGHT)
    raw = bytes(image.constBits())
    stride, width = image.bytesPerLine(), image.width()
    return [
        (raw[at], raw[at + 1], raw[at + 2], raw[at + 3])
        for y in range(image.height())
        for at in range(y * stride, y * stride + 4 * width, 4)
    ]


def _assert_matches_the_drawing(packaged: QImage, size: int) -> None:
    """Exact (2 levels) inside flat areas; up to 8 levels on anti-aliased edges, which Qt
    versions may rasterize a bit differently. A color or geometry change fails."""
    assert (packaged.width(), packaged.height()) == (size, size)
    shipped, drawn = _pixels(packaged), _pixels(icons.brand_image(size))

    def delta(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> int:
        return max(abs(p - q) for p, q in zip(a, b, strict=True))

    for y in range(size):
        for x in range(size):
            here = drawn[y * size + x]
            neighbours = [
                drawn[ny * size + nx]
                for ny in range(max(y - 1, 0), min(y + 2, size))
                for nx in range(max(x - 1, 0), min(x + 2, size))
            ]
            edge = any(delta(here, other) > 3 for other in neighbours)
            assert delta(shipped[y * size + x], here) <= (8 if edge else 2), (x, y)


def _ico_entries(data: bytes) -> dict[int, QImage]:
    """The images of an ICO file by size: PNG entries as they are, 32-bit BMP entries decoded."""
    _reserved, _kind, count = struct.unpack_from("<HHH", data)
    entries: dict[int, QImage] = {}
    for index in range(count):
        width, _height, _, _, _, _bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        side = width or 256
        blob = data[offset : offset + length]
        if blob.startswith(b"\x89PNG\r\n\x1a\n"):
            entries[side] = QImage.fromData(blob)
            continue
        header_size = struct.unpack_from("<I", blob)[0]
        stride = side * 4
        rows = [blob[header_size + row * stride : header_size + (row + 1) * stride] for row in range(side)]
        pixels = b"".join(reversed(rows))  # bottom-up BGRA = ARGB32 on little-endian
        entries[side] = QImage(pixels, side, side, stride, _STRAIGHT).copy()
    return entries


@pytest.mark.parametrize("size", [16, 24, 32, 48, 64, 256])
def test_packaged_icons_match_the_drawn_mark(size: int) -> None:
    """`python -m tools.gen_icon` was re-run after the last change to the drawing."""
    _assert_matches_the_drawing(QImage(str(icons.ASSET_ICON.parent / f"voicemate-{size}.png")), size)
    _assert_matches_the_drawing(_ico_entries(icons.ASSET_ICON.read_bytes())[size], size)


def test_taskbar_tone_follows_the_windows_registry(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(icons.sys, "platform", "win32")
    monkeypatch.setattr(icons, "_windows_taskbar_is_light", lambda: True)
    assert icons.taskbar_glyph_tone() == "dark"
    monkeypatch.setattr(icons, "_windows_taskbar_is_light", lambda: False)
    assert icons.taskbar_glyph_tone() == "light"
