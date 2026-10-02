from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.platform import clipboard as cb
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


def test_wsl_prefers_wl_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """wl-copy (native to WSLg) comes before everything else when present."""
    monkeypatch.setattr(cb.shutil, "which", lambda n: "/usr/bin/wl-copy" if n == "wl-copy" else None)
    runs: list[tuple[Any, Any]] = []
    monkeypatch.setattr(cb.subprocess, "run", lambda cmd, **kw: runs.append((cmd, kw.get("input"))))
    WslClipboardWriter().copy("ação")
    assert runs[0][0] == ["wl-copy"]
    assert runs[0][1] == "ação".encode()  # UTF-8 for wl-copy


def test_wsl_falls_back_to_pyperclip_when_no_native(monkeypatch: pytest.MonkeyPatch) -> None:
    import pyperclip

    monkeypatch.setattr(cb.shutil, "which", lambda n: None)
    monkeypatch.setattr(cb, "_WINDOWS_CLIP_EXE", Path("/nonexistent/clip.exe"))
    copied: list[str] = []
    monkeypatch.setattr(pyperclip, "copy", copied.append)
    WslClipboardWriter().copy("oi")
    assert copied == ["oi"]


def test_wsl_clip_exe_is_last_resort_utf16(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import pyperclip

    fake = tmp_path / "clip.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(cb.shutil, "which", lambda n: None)
    monkeypatch.setattr(cb, "_WINDOWS_CLIP_EXE", fake)
    monkeypatch.setattr(cb, "_interop_works", lambda: True)  # working interop → clip.exe is a candidate

    def _boom(_t: str) -> None:
        raise RuntimeError("sem display")

    monkeypatch.setattr(pyperclip, "copy", _boom)
    runs: list[tuple[Any, Any]] = []
    monkeypatch.setattr(cb.subprocess, "run", lambda cmd, **kw: runs.append((cmd, kw.get("input"))))
    WslClipboardWriter().copy("ação")
    assert runs[-1][0] == [str(fake)]
    assert runs[-1][1].startswith(b"\xff\xfe")  # BOM UTF-16LE
    assert "ação".encode("utf-16-le") in runs[-1][1]


def test_wsl_clip_exe_exec_format_error_raises_with_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """clip.exe present but with Exec format error (binfmt missing) → instructive error."""
    import pyperclip

    fake = tmp_path / "clip.exe"
    fake.write_bytes(b"")
    monkeypatch.setattr(cb.shutil, "which", lambda n: None)
    monkeypatch.setattr(cb, "_WINDOWS_CLIP_EXE", fake)
    monkeypatch.setattr(cb, "_interop_works", lambda: True)

    def _no_display(_t: str) -> None:
        raise RuntimeError("no display")

    monkeypatch.setattr(pyperclip, "copy", _no_display)

    def _osfail(cmd: list[str], **kw: object) -> None:
        raise OSError(8, "Exec format error")

    monkeypatch.setattr(cb.subprocess, "run", _osfail)
    with pytest.raises(RuntimeError, match="wl-clipboard"):
        WslClipboardWriter().copy("x")


def test_wsl_caches_working_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"which": 0}

    def _which(n: str) -> str | None:
        calls["which"] += 1
        return "/usr/bin/wl-copy" if n == "wl-copy" else None

    monkeypatch.setattr(cb.shutil, "which", _which)
    monkeypatch.setattr(cb.subprocess, "run", lambda cmd, **kw: None)
    writer = WslClipboardWriter()
    writer.copy("a")
    after_first = calls["which"]
    writer.copy("b")  # 2nd write uses the cached writer — does not re-scan
    assert calls["which"] == after_first


def test_wsl_self_heals_when_cached_writer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the cached mechanism (wl-copy) starts failing during a long session, the
    writer re-resolves and falls back to another — it does not stay stuck on what broke."""
    import pyperclip

    state = {"wl_ok": True}

    def _wl(text: str) -> None:
        if not state["wl_ok"]:
            raise RuntimeError("wayland indisponível")

    copied: list[str] = []
    monkeypatch.setattr(cb, "_wl_copy", _wl)
    monkeypatch.setattr(cb.shutil, "which", lambda n: "/usr/bin/wl-copy" if n == "wl-copy" else None)
    monkeypatch.setattr(pyperclip, "copy", copied.append)

    writer = WslClipboardWriter()
    writer.copy("primeiro")  # wl-copy works and is cached
    assert writer._writer_name == "wl-copy"

    state["wl_ok"] = False  # the WSLg bridge goes down
    writer.copy("segundo")  # cached one fails → re-resolves → falls back to pyperclip
    assert copied == ["segundo"]
    assert writer._writer_name == "pyperclip"


def test_clip_exe_not_offered_when_interop_broken(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without WSLInterop, clip.exe 'succeeds' via /bin/sh without copying anything (false
    positive). It cannot be a candidate — otherwise the app thinks it copied when it did not."""
    fake_clip = tmp_path / "clip.exe"
    fake_clip.write_bytes(b"")
    monkeypatch.setattr(cb.shutil, "which", lambda n: None)  # no wl-copy/xclip/clip.exe on PATH
    monkeypatch.setattr(cb, "_WINDOWS_CLIP_EXE", fake_clip)
    monkeypatch.setattr(cb, "_interop_works", lambda: False)
    names = [name for name, _ in WslClipboardWriter()._candidates()]
    assert "clip.exe" not in names

    monkeypatch.setattr(cb, "_interop_works", lambda: True)
    names = [name for name, _ in WslClipboardWriter()._candidates()]
    assert "clip.exe" in names


def test_wsl_writer_records_to_session_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """The text goes to the SessionStatus (so Windows can set the clipboard via /result),
    regardless of whether the local mechanism (wl-copy) works or not."""
    from app.core.session_status import SessionStatus

    status = SessionStatus()
    monkeypatch.setattr(cb.shutil, "which", lambda n: "/usr/bin/wl-copy" if n == "wl-copy" else None)
    monkeypatch.setattr(cb.subprocess, "run", lambda cmd, **kw: None)
    WslClipboardWriter(status).copy("transcrição")
    assert status.get() == (1, "transcrição")


def test_factory_by_platform() -> None:
    assert isinstance(create_clipboard_writer("windows"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("linux-x11"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("linux-wayland"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("wsl2"), WslClipboardWriter)
