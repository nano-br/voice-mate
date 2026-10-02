"""The VoiceMate mark and the tray glyphs (app/companion/ui/icons.py, docs/brand.md)."""

from __future__ import annotations

import itertools
import struct
from pathlib import Path
from typing import get_args

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.companion.contract import TrayState  # noqa: E402
from app.companion.ui import icons  # noqa: E402

ALL_STATES: tuple[TrayState, ...] = get_args(TrayState)
_STRAIGHT = QImage.Format.Format_ARGB32


def _image(state: TrayState, size: int) -> QImage:
    return icons.tray_pixmap(state, size).toImage().convertToFormat(_STRAIGHT)


def _luma(image: QImage, x: int, y: int) -> float:
    color = image.pixelColor(x, y)
    return 0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()


def _planes(state: TrayState, size: int) -> list[tuple[float, int]]:
    """(luma, alpha) per pixel: what is left of the icon for someone who sees no hue."""
    image = _image(state, size)
    return [(_luma(image, x, y), image.pixelColor(x, y).alpha()) for y in range(size) for x in range(size)]


def _visible_difference(first: list[tuple[float, int]], second: list[tuple[float, int]]) -> int:
    """Pixels that differ clearly: a big step in luma, or in coverage (the silhouette)."""
    return sum(
        1
        for (luma_a, alpha_a), (luma_b, alpha_b) in zip(first, second, strict=True)
        if abs(alpha_a - alpha_b) >= 128 or (min(alpha_a, alpha_b) >= 128 and abs(luma_a - luma_b) >= 48)
    )


def _marks(state: TrayState, size: int) -> list[bool]:
    """The bright marks on the tile (wave and status light) or the badge's cut-out: the shapes."""
    image = _image(state, size)
    return [image.pixelColor(x, y).alpha() < 128 or _luma(image, x, y) >= 120 for y in range(size) for x in range(size)]


# At 16 px the status light is about 4 x 4 pixels: ask for a clear difference in at least
# 6 of them, a bit more at the bigger sizes.
_MIN_DIFFERENT_PIXELS = {16: 6, 20: 6, 24: 8, 32: 10}


def test_every_state_has_a_signal() -> None:
    assert set(icons.STATE_SIGNALS) == set(ALL_STATES)
    # Every state the tray distinguishes keeps a shape of its own.
    assert len(set(icons.STATE_SIGNALS.values())) == len(ALL_STATES)


@pytest.mark.parametrize("size", icons.TRAY_SIZES)
@pytest.mark.parametrize("state", ALL_STATES)
def test_every_state_renders_at_every_tray_size(qapp: QApplication, state: TrayState, size: int) -> None:
    pixmap = icons.tray_pixmap(state, size)
    assert not pixmap.isNull()
    assert (pixmap.width(), pixmap.height()) == (size, size)
    image = pixmap.toImage().convertToFormat(_STRAIGHT)
    assert image.pixelColor(size // 4, size // 2).alpha() == 255  # the tile is there
    assert image.pixelColor(0, 0).alpha() < 255  # rounded corner


@pytest.mark.parametrize("size", icons.TRAY_SIZES)
def test_every_state_draws_a_different_icon(qapp: QApplication, size: int) -> None:
    images = {state: _image(state, size) for state in ALL_STATES}
    for first, second in itertools.combinations(ALL_STATES, 2):
        assert images[first] != images[second], f"{first}/{second} at {size} px"


@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_every_tray_state_is_distinguishable_without_color(qapp: QApplication, size: int) -> None:
    planes = {state: _planes(state, size) for state in ALL_STATES}
    for first, second in itertools.combinations(ALL_STATES, 2):
        different = _visible_difference(planes[first], planes[second])
        assert different >= _MIN_DIFFERENT_PIXELS[size], f"{first}/{second}: {different} px at {size} px"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("stopped", "idle"),
        ("starting", "restarting"),
        ("idle", "ready"),
        ("idle", "recording"),
        ("thinking", "speaking"),
        ("warning", "error"),
    ],
)
@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_related_states_differ_by_shape(qapp: QApplication, first: TrayState, second: TrayState, size: int) -> None:
    assert _marks(first, size) != _marks(second, size)


def test_tray_icon_has_every_size(qapp: QApplication) -> None:
    icon = icons.tray_icon("recording")
    available = {size.width() for size in icon.availableSizes()}
    assert set(icons.TRAY_SIZES) <= available


def test_state_pixmap_paints_for_the_device_pixel_ratio(qapp: QApplication) -> None:
    pixmap = icons.state_pixmap("recording", 40, 1.5)
    assert (pixmap.width(), pixmap.height()) == (60, 60)
    assert pixmap.devicePixelRatio() == 1.5


def test_idle_tray_glyph_is_the_app_icon(qapp: QApplication) -> None:
    for size in (16, 20, 24, 32):
        assert _image("idle", size) == icons.app_pixmap(size).toImage().convertToFormat(_STRAIGHT)


def test_speech_bubble_has_no_seam_between_body_and_tail() -> None:
    # 64 px = the 32-unit design x 2: the body ends at y = 20.5 px and the tail starts
    # 1 px above it. Painted as two subpaths, the odd-even fill left a hole there.
    image = icons.brand_image(64, "speaking").convertToFormat(_STRAIGHT)
    for y in range(17, 22):
        for x in range(29, 33):
            assert image.pixelColor(x, y).name() == "#22d3ee", (x, y)


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
