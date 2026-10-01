"""Compile the companion's Windows installer with Inno Setup (`make companion-installer`).

Finds ISCC.exe (Inno Setup 6.3 or newer) given by `--iscc`, on PATH or in the usual
install folders, and compiles packaging/windows/voicemate-companion.iss. When Inno
Setup is missing it says how to install it instead of failing with a bare
"command not found", the same from PowerShell, cmd or Git Bash.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packaging" / "windows" / "voicemate-companion.iss"
INSTALL_HINT = """\
ISCC.exe (Inno Setup 6.3 or newer) was not found. Install it with:
    winget install JRSoftware.InnoSetup
then open a new terminal and run `make companion-installer` again, or point to it with
    make companion-installer ISCC="C:/path/to/ISCC.exe"
"""


def find_iscc(explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit) if Path(explicit).is_file() else None
    on_path = shutil.which("ISCC")
    if on_path:
        return Path(on_path)
    # winget installs per user under %LOCALAPPDATA%\Programs; the classic installer
    # goes to Program Files (x86).
    for variable in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(variable)
        if not base:
            continue
        for folder in ("Programs/Inno Setup 6", "Inno Setup 6"):
            candidate = Path(base) / folder / "ISCC.exe"
            if candidate.is_file():
                return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--iscc", help="path to ISCC.exe (default: search PATH and the usual folders)")
    args = parser.parse_args()
    iscc = find_iscc(args.iscc)
    if iscc is None:
        if args.iscc:
            print(f"ISCC.exe not found at {args.iscc}")
        else:
            print(INSTALL_HINT, end="")
        return 1
    print(f"Compiling {SCRIPT.relative_to(ROOT)} with {iscc}")
    return subprocess.run([str(iscc), str(SCRIPT)], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
