"""Packaging invariants of the companion: one dependency list, pinned builds, the
identities the installer shares with the running app, and how the installer build
finds Inno Setup."""

from __future__ import annotations

import re
import struct
import tomllib
from pathlib import Path
from typing import Any

import pytest

from app.companion import paths
from app.companion.contract import (
    APP_USER_MODEL_ID,
    AUTOSTART_RUN_VALUE,
    INSTALLER_APP_MUTEX,
    SINGLE_INSTANCE_MUTEX,
)
from tools import build_installer

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
    # Different values = a second taskbar button next to the pin, an installer that
    # does not notice the running app, or two "start at sign-in" entries.
    assert _iss_define("AppUserModelID") == APP_USER_MODEL_ID
    assert _iss_define("AppMutex") == INSTALLER_APP_MUTEX
    assert SINGLE_INSTANCE_MUTEX == "Local\\" + INSTALLER_APP_MUTEX
    assert _iss_define("RunValueName") == AUTOSTART_RUN_VALUE


def _iss_section(name: str) -> list[str]:
    """The non-comment lines of an .iss section."""
    lines: list[str] = []
    inside = False
    for raw in _ISS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            inside = line == f"[{name}]"
            continue
        if inside and line and not line.startswith(";"):
            lines.append(line)
    return lines


def test_uninstaller_removes_the_not_copied_list() -> None:
    # pending.json holds transcription text (app/companion/pending_store.py): it, its temp
    # file and its .broken-* backups must not outlive the app in the user profile.
    assert paths.PENDING_FILE_NAME == "pending.json"
    entries = _iss_section("UninstallDelete")
    assert r'Type: files; Name: "{localappdata}\{#AppName}\pending.json*"' in entries
    # Before the folder itself, which only goes when it is empty.
    pending = next(i for i, line in enumerate(entries) if "pending.json" in line)
    folder = entries.index(r'Type: dirifempty; Name: "{localappdata}\{#AppName}"')
    assert pending < folder


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


def _fake_iscc(folder: Path) -> Path:
    folder.mkdir(parents=True)
    iscc = folder / "ISCC.exe"
    iscc.touch()
    return iscc


@pytest.fixture
def install_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Empty per-user and machine-wide install roots, and no ISCC on PATH."""
    roots = {variable: tmp_path / variable for variable in ("LOCALAPPDATA", "ProgramFiles(x86)", "ProgramFiles")}
    for variable, root in roots.items():
        root.mkdir()
        monkeypatch.setenv(variable, str(root))
    monkeypatch.setattr(build_installer.shutil, "which", lambda _name: None)
    return roots


def test_find_iscc_takes_the_newest_inno_setup_across_roots(install_roots: dict[str, Path]) -> None:
    _fake_iscc(install_roots["LOCALAPPDATA"] / "Programs" / "Inno Setup 6")  # per user
    newest = _fake_iscc(install_roots["ProgramFiles(x86)"] / "Inno Setup 7")  # machine-wide
    _fake_iscc(install_roots["ProgramFiles"] / "Inno Setup 5")
    assert build_installer.find_iscc(None) == newest


def test_find_iscc_compares_versions_as_numbers(install_roots: dict[str, Path]) -> None:
    _fake_iscc(install_roots["ProgramFiles(x86)"] / "Inno Setup 9")
    newest = _fake_iscc(install_roots["LOCALAPPDATA"] / "Programs" / "Inno Setup 10")
    assert build_installer.find_iscc(None) == newest


def test_find_iscc_without_inno_setup_or_with_a_wrong_path(install_roots: dict[str, Path], tmp_path: Path) -> None:
    assert build_installer.find_iscc(None) is None
    assert build_installer.find_iscc(str(tmp_path / "missing" / "ISCC.exe")) is None
