"""Draw the VoiceMate app icon (a white microphone on an indigo tile) with QPainter.

Writes `app/companion/assets/voicemate-<size>.png` for every size and
`app/companion/assets/voicemate.ico` with all of them: 16..64 as 32-bit BMP
entries and 256 as PNG, the classic layout that every Windows tool reads
(PyInstaller and Inno Setup included).

Each size is drawn on its own pixel grid instead of being downscaled: the 16 px
design puts every straight edge on a pixel boundary (32, 48, 64 and 256 are exact
multiples of it) and 24 px has its own design, so the small icons stay crisp in
the tray and the taskbar. The output is deterministic, so re-running it only
changes files when the drawing changes.

Run it from the repository root:
    Windows: .venv-companion\\Scripts\\python -m tools.gen_icon
    Linux:   poetry run python -m tools.gen_icon   (needs the `ui` extra)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ASSETS_DIR = Path(__file__).resolve().parents[1] / "app" / "companion" / "assets"
SIZES = (16, 24, 32, 48, 64, 256)
_PNG_ENTRY_MIN_SIZE = 256  # ICO entries this big are stored as PNG, smaller ones as BMP

_TILE_FLAT = QColor("#4f46e5")  # small sizes: a flat tile reads cleaner than a gradient
_TILE_TOP = QColor("#6366f1")
_TILE_BOTTOM = QColor("#4338ca")
_GLYPH = QColor("#ffffff")


@dataclass(frozen=True)
class _Design:
    """Microphone geometry in pixels of a `grid` x `grid` icon (y grows downwards)."""

    grid: int
    corner: float  # tile corner radius
    capsule: tuple[float, float, float, float]  # left, top, right, bottom
    stroke: float  # holder thickness
    gap: float  # space between the capsule and the holder
    arm_top: float  # where the holder arms end at the top
    stem: tuple[float, float, float, float]
    base: tuple[float, float, float, float]
    # Radius of the holder's bottom corners; None = a full half circle. At 16 px a half
    # circle blurs into the capsule, so that size gets a U with a straight bottom.
    holder_corner: float | None = None


# 12 x 8 px glyph centred on the 16 px tile: capsule 4 x 7, 1 px holder, 2 px stem.
# Scaled by 2, 3, 4 and 16 for the 32, 48, 64 and 256 px icons.
_DESIGN_16 = _Design(
    grid=16,
    corner=3,
    capsule=(6, 2, 10, 9),
    stroke=1,
    gap=1,
    arm_top=5,
    stem=(7, 11, 9, 13),
    base=(5, 13, 11, 14),
)
_DESIGN_16_PX = replace(_DESIGN_16, holder_corner=1.5)  # the 16 px icon itself
# 24 px is 1.5 x 16, which would put edges on half pixels: it gets its own grid.
_DESIGN_24 = _Design(
    grid=24,
    corner=4.5,
    capsule=(9, 3, 15, 13),
    stroke=2,
    gap=1,
    arm_top=7,
    stem=(11, 16, 13, 19),
    base=(8, 19, 16, 21),
)


def _rect(box: tuple[float, float, float, float]) -> QRectF:
    left, top, right, bottom = box
    return QRectF(left, top, right - left, bottom - top)


def _draw(painter: QPainter, design: _Design, gradient: bool) -> None:
    grid = design.grid
    painter.setPen(Qt.PenStyle.NoPen)
    if gradient:
        fill = QLinearGradient(0, 0, 0, grid)
        fill.setColorAt(0.0, _TILE_TOP)
        fill.setColorAt(1.0, _TILE_BOTTOM)
        painter.setBrush(fill)
    else:
        painter.setBrush(_TILE_FLAT)
    painter.drawRoundedRect(QRectF(0, 0, grid, grid), design.corner, design.corner)

    painter.setBrush(_GLYPH)
    capsule = _rect(design.capsule)
    half_width = capsule.width() / 2
    painter.drawRoundedRect(capsule, half_width, half_width)
    painter.drawRect(_rect(design.stem))
    base = _rect(design.base)
    painter.drawRoundedRect(base, base.height() / 2, base.height() / 2)

    # The holder: a U around the bottom of the capsule, concentric with its lower end.
    radius = half_width + design.gap + design.stroke / 2  # of the stroke's centre line
    left = capsule.center().x() - radius
    right = capsule.center().x() + radius
    bottom = capsule.bottom() - half_width + radius
    corner = radius if design.holder_corner is None else design.holder_corner
    top = design.arm_top + design.stroke / 2  # the round cap adds stroke / 2 above it
    holder = QPainterPath()
    holder.moveTo(left, top)
    holder.lineTo(left, bottom - corner)
    holder.arcTo(QRectF(left, bottom - 2 * corner, 2 * corner, 2 * corner), 180, 90)
    holder.lineTo(right - corner, bottom)
    holder.arcTo(QRectF(right - 2 * corner, bottom - 2 * corner, 2 * corner, 2 * corner), 270, 90)
    holder.lineTo(right, top)
    pen = QPen(_GLYPH, design.stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(holder)


def render(size: int) -> QImage:
    """The icon at `size` px, straight (non-premultiplied) ARGB32."""
    design = {16: _DESIGN_16_PX, 24: _DESIGN_24}.get(size, _DESIGN_16)
    if size % design.grid:
        raise ValueError(f"{size} px is not a multiple of the {design.grid} px design grid")
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / design.grid, size / design.grid)
    _draw(painter, design, gradient=size >= 32)
    painter.end()
    return image.convertToFormat(QImage.Format.Format_ARGB32)


def _bmp_entry(image: QImage) -> bytes:
    """A 32-bit ICO image: BITMAPINFOHEADER, bottom-up BGRA rows, then the AND mask."""
    width, height = image.width(), image.height()
    pixels = bytes(image.constBits())  # ARGB32 on little-endian = B, G, R, A per pixel
    stride = image.bytesPerLine()
    rows = [pixels[y * stride : y * stride + width * 4] for y in range(height)]
    mask_stride = (width + 31) // 32 * 4
    mask_rows = []
    for row in rows:
        bits = bytearray(mask_stride)
        for x in range(width):
            if row[x * 4 + 3] == 0:  # fully transparent: set in the mask too, for old readers
                bits[x // 8] |= 0x80 >> (x % 8)
        mask_rows.append(bytes(bits))
    xor = b"".join(reversed(rows))
    mask = b"".join(reversed(mask_rows))
    header = struct.pack("<IiiHHIIiiII", 40, width, height * 2, 1, 32, 0, len(xor) + len(mask), 0, 0, 0, 0)
    return header + xor + mask


def ico_bytes(entries: list[tuple[int, bytes]]) -> bytes:
    """An ICO file from (size, image data) pairs, smallest first."""
    directory = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(directory) + 16 * len(entries)
    blobs = []
    for size, data in entries:
        side = 0 if size >= 256 else size  # 0 means 256 in the ICO directory
        directory += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs.append(data)
    return directory + b"".join(blobs)


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[int, bytes]] = []
    for size in SIZES:
        image = render(size)
        png = ASSETS_DIR / f"voicemate-{size}.png"
        if not image.save(str(png)):  # format from the .png suffix
            raise RuntimeError(f"could not write {png}")
        entries.append((size, png.read_bytes() if size >= _PNG_ENTRY_MIN_SIZE else _bmp_entry(image)))
        print(f"wrote {png.relative_to(ASSETS_DIR.parents[2])}")
    ico = ASSETS_DIR / "voicemate.ico"
    ico.write_bytes(ico_bytes(entries))
    print(f"wrote {ico.relative_to(ASSETS_DIR.parents[2])} ({', '.join(str(size) for size in SIZES)} px)")


if __name__ == "__main__":
    main()
