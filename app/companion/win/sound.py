"""Cue playback on Windows: `winsound`, async, from a rendered WAV file.

`SND_MEMORY` cannot be async, hence the files (docs/companion-app.md, "Sound cues").
One sound at a time per process: a new cue cuts the previous one.
"""

from __future__ import annotations

import sys
import winsound
from pathlib import Path

assert sys.platform == "win32"  # also tells type checkers this module is Windows-only


class WinSound:
    def play(self, path: Path) -> None:
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def stop(self) -> None:
        winsound.PlaySound(None, 0)
