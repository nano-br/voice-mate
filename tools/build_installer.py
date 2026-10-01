"""Compile the companion's Windows installer with Inno Setup (`make companion-installer`).

Finds ISCC.exe (Inno Setup 6.3 or newer) given by `--iscc`, on PATH or in the usual
install folders, and compiles packaging/windows/voicemate-companion.iss. When Inno
Setup is missing it says how to install it instead of failing with a bare
"command not found", the same from PowerShell, cmd or Git Bash.
"""

from __future__ import annotations

import argparse
import os
import re
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
    # A per-user install goes under %LOCALAPPDATA%\Programs, a machine-wide one under
    # Program Files (x86) or Program Files; the folder carries the major version
    # ("Inno Setup 6"), so take the newest one found in any of them.
    candidates = [
        iscc
        for variable in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles")
        if (base := os.environ.get(variable))
        for parent in (Path(base) / "Programs", Path(base))
        for iscc in parent.glob("Inno Setup */ISCC.exe")
    ]
    return max(candidates, key=_folder_version, default=None)


def _folder_version(iscc: Path) -> list[int]:
    return [int(number) for number in re.findall(r"\d+", iscc.parent.name)]


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
