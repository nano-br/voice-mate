from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.platform.clipboard import (
    PyperclipWriter,
    WslClipboardWriter,
    create_clipboard_writer,
)


def test_pyperclip_writer_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyperclip

    copied: list[str] = []
    monkeypatch.setattr(pyperclip, "copy", copied.append)
    PyperclipWriter().copy("olá")
    assert copied == ["olá"]


def test_wsl_writer_prefers_pyperclip(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyperclip

    copied: list[str] = []
    monkeypatch.setattr(pyperclip, "copy", copied.append)
    writer = WslClipboardWriter()
    writer.copy("olá")
    assert copied == ["olá"]


def test_wsl_writer_falls_back_to_clip_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyperclip

    from app.platform import clipboard as clipboard_module

    def _boom(_text: str) -> None:
        raise RuntimeError("sem xclip")

    runs: list[tuple[Any, ...]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> None:  # noqa: ANN401
        runs.append((cmd, kwargs.get("input")))

    monkeypatch.setattr(pyperclip, "copy", _boom)
    monkeypatch.setattr(subprocess, "run", _fake_run)
    monkeypatch.setattr(clipboard_module.shutil, "which", lambda name: "clip.exe")
    writer = WslClipboardWriter()
    writer.copy("ação")  # acentos: precisa sair como UTF-16LE com BOM
    writer.copy("segunda")  # segunda chamada nem tenta pyperclip de novo

    assert len(runs) == 2
    cmd, payload = runs[0]
    assert cmd == ["clip.exe"]
    assert isinstance(payload, bytes)
    assert payload.startswith(b"\xff\xfe")  # BOM UTF-16LE
    assert "ação".encode("utf-16-le") in payload


def test_wsl_writer_resolves_interop_path_when_not_in_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """appendWindowsPath=false tira o clip.exe do PATH — o writer precisa achar
    o caminho absoluto via interop (/mnt/c/Windows/System32/clip.exe)."""
    from app.platform import clipboard as clipboard_module

    fake_clip = tmp_path / "clip.exe"
    fake_clip.write_bytes(b"")
    runs: list[list[str]] = []
    monkeypatch.setattr(clipboard_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(clipboard_module, "_WINDOWS_CLIP_EXE", fake_clip)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: runs.append(cmd))

    writer = WslClipboardWriter()
    writer._copy_via_clip_exe("texto")

    assert runs == [[str(fake_clip)]]


def test_wsl_writer_clear_error_when_clip_exe_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from app.platform import clipboard as clipboard_module

    monkeypatch.setattr(clipboard_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(clipboard_module, "_WINDOWS_CLIP_EXE", tmp_path / "nope.exe")

    writer = WslClipboardWriter()
    with pytest.raises(RuntimeError, match="interop|wl-clipboard"):
        writer._copy_via_clip_exe("texto")


def test_factory_by_platform() -> None:
    assert isinstance(create_clipboard_writer("windows"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("linux-x11"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("linux-wayland"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("wsl2"), WslClipboardWriter)
