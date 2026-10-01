"""The companion ships alone on Windows: it must never pull in the engine's stack."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = (
    "numpy",
    "sounddevice",
    "torch",
    "faster_whisper",
    "ctranslate2",
    "whisper",
    "pyperclip",
    "keyboard",
    "mouse",
    "pynput",
    "evdev",
    "app.core",
    "app.features",
    "app.cli",
    "app.setup",
)

# Runs in a fresh interpreter (this test process may already have numpy etc. loaded).
# Walks every module of app.companion and app.protocol; Qt modules only where PySide6
# is installed, Windows modules only on Windows, Linux modules only on Linux.
_PROBE = (
    "import importlib, importlib.util, pkgutil, sys\n"
    "import app.companion, app.protocol\n"
    "has_qt = importlib.util.find_spec('PySide6') is not None\n"
    "def skip(name):\n"
    "    parts = name.split('.')\n"
    "    if ('ui' in parts or parts[-1] == 'main') and not has_qt:\n"
    "        return True\n"
    "    if 'win' in parts and sys.platform != 'win32':\n"
    "        return True\n"
    "    return 'linux' in parts and not sys.platform.startswith('linux')\n"
    "def walk(pkg):\n"
    "    for info in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + '.'):\n"
    "        if not skip(info.name):\n"
    "            mod = importlib.import_module(info.name)\n"
    "            if info.ispkg:\n"
    "                walk(mod)\n"
    "for pkg in (app.companion, app.protocol):\n"
    "    walk(pkg)\n"
    f"forbidden = {_FORBIDDEN!r}\n"
    "print(','.join(sorted(m for m in sys.modules if any(m == f or m.startswith(f + '.') for f in forbidden))))\n"
)


def test_companion_and_protocol_import_without_the_engine() -> None:
    result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True, cwd=_ROOT)
    out = result.stdout.strip()
    assert out == "", f"companion imports engine modules: {out}"
