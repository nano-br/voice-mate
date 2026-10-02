"""The companion's version, derived from pyproject.toml (the single source, docs/releasing.md).

The companion runs from three places, and each one finds the version differently:

- the Windows build (PyInstaller): the spec bakes the pyproject version into a
  `VERSION` file next to this module, because the frozen app has no pyproject.toml
  and no installed distribution metadata;
- a source checkout (`make run-tray`, the tests): pyproject.toml at the repository
  root, which is never stale after a version bump;
- an installed package (`poetry install --extras ui` on Linux): its distribution
  metadata.

"0.0.0" only when none of them is there, so the app still starts.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path
from typing import Final

DISTRIBUTION: Final = "voice-mate"
FALLBACK_VERSION: Final = "0.0.0"
BAKED_VERSION_FILE: Final = Path(__file__).with_name("VERSION")
PYPROJECT_FILE: Final = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _baked_version(path: Path) -> str | None:
    try:
        version = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return version or None


def _pyproject_version(path: Path) -> str | None:
    try:
        with path.open("rb") as file:
            poetry = tomllib.load(file)["tool"]["poetry"]
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return None
    # Only this project's pyproject: an installed package has someone else's (or none) above it.
    if not isinstance(poetry, dict) or poetry.get("name") != DISTRIBUTION:
        return None
    version = poetry.get("version")
    return version if isinstance(version, str) and version else None


def _installed_version() -> str | None:
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return None


def companion_version(
    baked_file: Path = BAKED_VERSION_FILE,
    pyproject_file: Path = PYPROJECT_FILE,
) -> str:
    return _baked_version(baked_file) or _pyproject_version(pyproject_file) or _installed_version() or FALLBACK_VERSION
