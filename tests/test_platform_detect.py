from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.platform import detect
from app.platform.detect import default_trigger, detect_platform


def _fake_proc_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str | None) -> None:
    proc = tmp_path / "version"
    if content is not None:
        proc.write_text(content, encoding="utf-8")
    monkeypatch.setattr(detect, "_PROC_VERSION", proc)


def test_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert detect_platform() == "windows"


def test_wsl2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _fake_proc_version(monkeypatch, tmp_path, "Linux version 6.6.87.2-microsoft-standard-WSL2 ...")
    # WSLg exporta WAYLAND_DISPLAY — WSL deve vencer a checagem de Wayland.
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert detect_platform() == "wsl2"


def test_linux_wayland_by_display(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _fake_proc_version(monkeypatch, tmp_path, "Linux version 6.8.0-generic ...")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert detect_platform() == "linux-wayland"


def test_linux_wayland_by_session_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _fake_proc_version(monkeypatch, tmp_path, "Linux version 6.8.0-generic ...")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert detect_platform() == "linux-wayland"


def test_linux_x11_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _fake_proc_version(monkeypatch, tmp_path, "Linux version 6.8.0-generic ...")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert detect_platform() == "linux-x11"


def test_missing_proc_version_is_not_wsl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    _fake_proc_version(monkeypatch, tmp_path, None)  # arquivo inexistente
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    assert detect_platform() == "linux-x11"


def test_default_triggers() -> None:
    assert default_trigger("windows") == "keyboard-hooks"
    assert default_trigger("linux-x11") == "pynput"
    assert default_trigger("linux-wayland") == "evdev"
    assert default_trigger("wsl2") == "socket"
