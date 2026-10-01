"""Packaging invariants of the companion: one dependency list, pinned builds, and the
identities the installer shares with the running app."""

from __future__ import annotations

import re
import struct
import tomllib
from pathlib import Path
from typing import Any

from app.companion.contract import APP_USER_MODEL_ID, INSTALLER_APP_MUTEX, SINGLE_INSTANCE_MUTEX

_ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENTS = _ROOT / "requirements"
_ISS = _ROOT / "packaging" / "windows" / "voicemate-companion.iss"
_ASSETS = _ROOT / "app" / "companion" / "assets"
_ICON_SIZES = {16, 24, 32, 48, 64, 256}

# name [extras] specifier ; marker
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*([^;]*?)\s*(?:;\s*(.+))?$")


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalize_marker(marker: str | None) -> str:
    return re.sub(r"\s+", " ", (marker or "").replace("'", '"')).strip()


def _requirements(path: Path) -> dict[str, tuple[str, str]]:
    """{canonical name: (specifier, marker)} of a requirements file, `-r`/`-c` lines skipped."""
    entries: dict[str, tuple[str, str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _REQUIREMENT.match(line)
        assert match, f"{path.name}: cannot parse {raw!r}"
        name, specifier, marker = match.groups()
        entries[_canonical(name)] = (specifier.replace(" ", ""), _normalize_marker(marker))
    return entries


def _poetry() -> dict[str, Any]:
    with (_ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)
    poetry: dict[str, Any] = pyproject["tool"]["poetry"]
    return poetry


def test_ui_extra_lists_the_companion_requirements() -> None:
    poetry = _poetry()
    requirements = _requirements(_REQUIREMENTS / "companion.txt")

    assert sorted(_canonical(name) for name in poetry["extras"]["ui"]) == sorted(requirements)
    declared = {_canonical(name): spec for name, spec in poetry["dependencies"].items()}
    for name, (specifier, marker) in requirements.items():
        spec = declared.get(name)
        assert isinstance(spec, dict), f"{name} must be an optional table in [tool.poetry.dependencies]"
        assert spec.get("optional") is True, f"{name} must be optional (only the `ui` extra installs it)"
        version = str(spec.get("version", "*"))
        assert (version if version != "*" else "") == specifier, f"{name}: {version!r} != {specifier!r}"
        assert _normalize_marker(spec.get("markers")) == marker, f"{name}: markers differ"


def test_tray_script_runs_the_companion() -> None:
    assert _poetry()["scripts"]["voice-mate-tray"] == "app.companion.main:main"


def test_constraints_pin_every_companion_package() -> None:
    pins = _requirements(_REQUIREMENTS / "companion-constraints.txt")
    for name, (specifier, _marker) in pins.items():
        assert re.fullmatch(r"==[\w.+!-]+", specifier), f"constraints must be exact pins: {name}{specifier}"
    wanted = _requirements(_REQUIREMENTS / "companion.txt") | _requirements(_REQUIREMENTS / "companion-dev.txt")
    assert sorted(set(wanted) - set(pins)) == []


def _iss_define(name: str) -> str:
    match = re.search(rf'^#define {name} "([^"]*)"$', _ISS.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"#define {name} not found in {_ISS.name}"
    return match.group(1)


def test_installer_shares_the_app_identity() -> None:
    # Different values = a second taskbar button next to the pin, or an installer
    # that does not notice the running app.
    assert _iss_define("AppUserModelID") == APP_USER_MODEL_ID
    assert _iss_define("AppMutex") == INSTALLER_APP_MUTEX
    assert SINGLE_INSTANCE_MUTEX == "Local\\" + INSTALLER_APP_MUTEX


def test_icon_has_every_size() -> None:
    data = (_ASSETS / "voicemate.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes: set[int] = set()
    for index in range(count):
        width, height, _, _, _, bits, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * index)
        side = width or 256
        assert (height or 256) == side and bits == 32
        image = data[offset : offset + length]
        if image.startswith(b"\x89PNG\r\n\x1a\n"):
            assert struct.unpack_from(">II", image, 16) == (side, side)
        else:
            assert struct.unpack_from("<Iii", image) == (40, side, 2 * side)  # BITMAPINFOHEADER
        sizes.add(side)
    assert sizes == _ICON_SIZES
    for size in _ICON_SIZES:
        assert (_ASSETS / f"voicemate-{size}.png").is_file()
