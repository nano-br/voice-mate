"""Ensure stdout/stderr use UTF-8 and flush every line.

On legacy Windows consoles (cp1252), printing emoji/→/accents raises
UnicodeEncodeError. Reconfiguring to UTF-8 with errors='replace' avoids the crash
without affecting terminals that are already UTF-8 (a no-op in that case).

Line buffering matters when stdout is a pipe (the companion app's supervisor, a
systemd journal): Python block-buffers pipes, so log lines would arrive in bursts,
minutes late, stamped with the time they were read instead of the time they happened.
"""

from __future__ import annotations

import sys
from typing import TextIO


def force_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8, line-buffered. Idempotent and failure-proof."""
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)


def _reconfigure(stream: TextIO | None) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # None (pythonw) or streams without reconfigure (e.g. io.StringIO)
        return
    try:
        reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (ValueError, OSError):
        pass
