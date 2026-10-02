"""Entry script of the frozen companion (VoiceMate.exe), see voicemate-companion.spec.

PyInstaller runs its entry script as `__main__`. Importing `app.companion.main` as a
regular module instead keeps one copy of it in the process (no `__main__` duplicate)
and does not depend on the module's own `if __name__ == "__main__"` block.
"""

from collections.abc import Callable

from app.companion.main import main

entry: Callable[[], object] = main  # same contract as the `voice-mate-tray` script: None or an exit code
raise SystemExit(entry())
