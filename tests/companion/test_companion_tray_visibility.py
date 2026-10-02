"""Tray icon visibility on Windows 11 (NotifyIconSettings), against a fake registry only."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pytest

from app.companion.win import tray_visibility
from app.companion.win.tray_visibility import set_tray_icon_promoted

OURS = r"C:\Users\me\AppData\Local\Programs\VoiceMate\VoiceMate.exe"
PROGRAM_FILES_X64 = "{6D809377-6AF0-444B-8957-A3773F02200E}"


@dataclass
class FakeRegistry:
    """subkey -> {"ExecutablePath": str, "IsPromoted": int}; None = no NotifyIconSettings key."""

    entries: dict[str, dict[str, object]] | None = field(default_factory=dict)
    writes: list[tuple[str, int]] = field(default_factory=list)
    fail_on: set[str] = field(default_factory=set)

    def subkeys(self) -> list[str]:
        if self.entries is None:
            raise FileNotFoundError("NotifyIconSettings")
        return list(self.entries)

    def executable_path(self, subkey: str) -> str | None:
        assert self.entries is not None
        value = self.entries[subkey].get("ExecutablePath")
        return value if isinstance(value, str) else None

    def is_promoted(self, subkey: str) -> int | None:
        assert self.entries is not None
        value = self.entries[subkey].get("IsPromoted")
        return value if isinstance(value, int) else None

    def set_promoted(self, subkey: str, value: int) -> None:
        assert self.entries is not None
        if subkey in self.fail_on:
            raise PermissionError("access denied")
        self.writes.append((subkey, value))
        self.entries[subkey]["IsPromoted"] = value


def _resolve(folder_id: str) -> str | None:
    return r"C:\Program Files" if folder_id.upper() == PROGRAM_FILES_X64 else None


def _registry() -> FakeRegistry:
    return FakeRegistry(
        {
            "100": {"ExecutablePath": r"{F38BF404-1D43-42F2-9305-67DE0B28FC23}\explorer.exe"},
            "200": {"ExecutablePath": r"C:\Users\me\AppData\Local\Microsoft\OneDrive\OneDrive.exe", "IsPromoted": 1},
            "300": {"ExecutablePath": OURS.upper()},  # Windows paths compare case-insensitively
            "400": {"ExecutablePath": r"C:\Users\me\AppData\Local\Programs\VoiceMate\Other.exe", "IsPromoted": 0},
            "500": {},  # no ExecutablePath at all
        }
    )


def test_promote_sets_is_promoted_only_on_our_entries() -> None:
    registry = _registry()
    assert set_tray_icon_promoted(True, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert registry.writes == [("300", 1)]
    assert registry.entries is not None
    assert registry.entries["400"]["IsPromoted"] == 0  # another executable: untouched
    assert registry.entries["200"]["IsPromoted"] == 1


def test_demote_writes_zero_and_skips_values_already_set() -> None:
    registry = _registry()
    assert registry.entries is not None
    registry.entries["301"] = {"ExecutablePath": OURS, "IsPromoted": 0}
    assert set_tray_icon_promoted(False, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert registry.writes == [("300", 0)]  # "301" was already 0: no write
    assert set_tray_icon_promoted(False, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert registry.writes == [("300", 0)]


def test_every_entry_of_our_executable_is_updated() -> None:
    registry = FakeRegistry({"1": {"ExecutablePath": OURS}, "2": {"ExecutablePath": OURS, "IsPromoted": 0}})
    assert set_tray_icon_promoted(True, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert sorted(registry.writes) == [("1", 1), ("2", 1)]


def test_known_folder_prefixes_are_resolved() -> None:
    registry = FakeRegistry(
        {
            "1": {"ExecutablePath": PROGRAM_FILES_X64 + r"\VoiceMate\VoiceMate.exe"},
            "2": {"ExecutablePath": r"{00000000-0000-0000-0000-000000000000}\VoiceMate\VoiceMate.exe"},
        }
    )
    ours = r"C:\Program Files\VoiceMate\VoiceMate.exe"
    assert set_tray_icon_promoted(True, executables=[ours], registry=registry, resolve_folder=_resolve)
    assert registry.writes == [("1", 1)]  # an unknown folder never matches


def test_relative_segments_are_normalized() -> None:
    registry = FakeRegistry({"1": {"ExecutablePath": r"C:\Users\me\AppData\Local\Programs\VoiceMate\.\VoiceMate.exe"}})
    assert set_tray_icon_promoted(True, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert registry.writes == [("1", 1)]


def test_no_entry_yet_returns_false_without_writing() -> None:
    registry = FakeRegistry({"200": {"ExecutablePath": r"C:\Other\app.exe"}})
    assert not set_tray_icon_promoted(True, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert registry.writes == []


def test_missing_key_is_a_no_op() -> None:
    """Windows 10 has no NotifyIconSettings key."""
    registry = FakeRegistry(None)
    assert not set_tray_icon_promoted(True, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert registry.writes == []


def test_never_raises() -> None:
    class Broken(FakeRegistry):
        def subkeys(self) -> list[str]:
            raise RuntimeError("registry exploded")

    assert not set_tray_icon_promoted(True, executables=[OURS], registry=Broken(), resolve_folder=_resolve)

    def bad_resolver(folder_id: str) -> str | None:
        raise ValueError(folder_id)

    registry = FakeRegistry({"1": {"ExecutablePath": PROGRAM_FILES_X64 + r"\x.exe"}})
    assert not set_tray_icon_promoted(True, executables=[OURS], registry=registry, resolve_folder=bad_resolver)


def test_a_refused_write_is_logged_and_the_rest_still_applies(caplog: pytest.LogCaptureFixture) -> None:
    registry = FakeRegistry({"1": {"ExecutablePath": OURS}, "2": {"ExecutablePath": OURS}}, fail_on={"1"})
    assert set_tray_icon_promoted(True, executables=[OURS], registry=registry, resolve_folder=_resolve)
    assert registry.writes == [("2", 1)]
    assert "could not be updated" in caplog.text


def test_no_executable_known_finds_nothing() -> None:
    registry = _registry()
    assert not set_tray_icon_promoted(True, executables=[], registry=registry, resolve_folder=_resolve)
    assert registry.writes == []


def test_default_executables_include_sys_executable() -> None:
    assert sys.executable in tray_visibility.own_executables()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_known_folder_path_resolves_the_windows_folder() -> None:
    windows = tray_visibility.known_folder_path("{F38BF404-1D43-42F2-9305-67DE0B28FC23}")  # FOLDERID_Windows
    assert windows is not None
    assert os.path.normcase(windows) == os.path.normcase(os.environ["SystemRoot"])
    assert tray_visibility.known_folder_path("{00000000-0000-0000-0000-000000000000}") is None
    assert tray_visibility.known_folder_path("not a guid") is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows API")
def test_process_image_path_is_this_interpreter() -> None:
    image = tray_visibility.process_image_path()
    assert image is not None and image.lower().endswith(".exe") and os.path.isfile(image)
