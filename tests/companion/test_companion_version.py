"""The companion's version comes from pyproject.toml, in the source tree and in the build."""

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

from app.companion import client, version

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "packaging" / "windows" / "voicemate-companion.spec"


def _pyproject_version() -> str:
    with (_ROOT / "pyproject.toml").open("rb") as file:
        return str(tomllib.load(file)["tool"]["poetry"]["version"])


def _no_installed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(version.metadata, "version", missing)


def test_the_client_announces_the_pyproject_version() -> None:
    assert client.COMPANION_VERSION == _pyproject_version()
    assert version.companion_version() == _pyproject_version()


def test_the_source_tree_has_no_baked_version_file() -> None:
    # Only the build writes it (into the bundle): one in the source tree would go stale.
    assert not version.BAKED_VERSION_FILE.exists()


def test_the_baked_file_wins(tmp_path: Path) -> None:
    baked = tmp_path / "VERSION"
    baked.write_bytes(b"1.2.3-rc.1\n")
    assert version.companion_version(baked_file=baked) == "1.2.3-rc.1"


def test_an_empty_baked_file_is_ignored(tmp_path: Path) -> None:
    baked = tmp_path / "VERSION"
    baked.write_bytes(b"\n")
    assert version.companion_version(baked_file=baked) == _pyproject_version()


def test_another_projects_pyproject_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(b'[tool.poetry]\nname = "something-else"\nversion = "9.9.9"\n')
    monkeypatch.setattr(version.metadata, "version", lambda _name: "4.5.6")
    assert version.companion_version(baked_file=tmp_path / "none", pyproject_file=pyproject) == "4.5.6"


@pytest.mark.parametrize("content", [b"not toml [", b"[tool]\n", b'[tool.poetry]\nname = "voice-mate"\n'])
def test_an_unusable_pyproject_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_bytes(content)
    _no_installed_version(monkeypatch)
    assert version.companion_version(baked_file=tmp_path / "none", pyproject_file=pyproject) == "0.0.0"


def test_without_any_source_the_version_is_the_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_installed_version(monkeypatch)
    missing = tmp_path / "missing"
    assert version.companion_version(baked_file=missing, pyproject_file=missing) == version.FALLBACK_VERSION


def test_the_spec_bakes_the_file_this_module_reads() -> None:
    spec = _SPEC.read_text(encoding="utf-8")
    # The bundle path of the file must be the folder of app/companion/version.py ...
    assert re.search(r'return \[\(str\(path\), "app/companion"\)\]', spec)
    # ... under the name the module looks for.
    assert f'path = out_dir / "{version.BAKED_VERSION_FILE.name}"' in spec
    assert "datas += baked_version_file(VERSION," in spec
