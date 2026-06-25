"""Ensure stdout/stderr use UTF-8 (the app prints emoji/→/accents all the time).

On legacy Windows consoles (cp1252), printing these characters raises
UnicodeEncodeError. Reconfiguring to UTF-8 with errors='replace' avoids the crash
without affecting terminals that are already UTF-8 (a no-op in that case).
"""

from __future__ import annotations

import sys
from typing import TextIO


def force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8. Idempotent and failure-proof."""
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)


def _reconfigure(stream: TextIO | None) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # streams without reconfigure (e.g. test capture)
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass
