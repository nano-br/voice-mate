"""Write the VoiceMate app icon: the M wave with the coral dot on the indigo tile.

The mark is drawn by `app.companion.ui.icons.brand_image` (the same code that paints
the tray glyphs and the windows' fallback icon), so the packaged icon can never drift
from what the app draws. See docs/brand.md for the identity.

Writes `app/companion/assets/voicemate-<size>.png` for every size and
`app/companion/assets/voicemate.ico` with all of them: 16..64 as 32-bit BMP
entries and 256 as PNG, the classic layout that every Windows tool reads
(PyInstaller and Inno Setup included).

Each size is drawn on its own pixel grid instead of being downscaled: 16 and 24 px
have their own designs with straight edges on pixel boundaries, the other sizes use
the smooth 32-unit geometry. The output is deterministic, so re-running it only
changes files when the drawing changes.

Run it from the repository root:
    Windows: .venv-companion\\Scripts\\python -m tools.gen_icon
    Linux:   poetry run python -m tools.gen_icon   (needs the `ui` extra)
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtGui import QImage

from app.companion.ui.icons import brand_image

ASSETS_DIR = Path(__file__).resolve().parents[1] / "app" / "companion" / "assets"
SIZES = (16, 24, 32, 48, 64, 256)
_PNG_ENTRY_MIN_SIZE = 256  # ICO entries this big are stored as PNG, smaller ones as BMP


def render(size: int) -> QImage:
    """The icon at `size` px, straight (non-premultiplied) ARGB32."""
    return brand_image(size).convertToFormat(QImage.Format.Format_ARGB32)


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
