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
    """wl-copy (nativo do WSLg) vem antes de tudo quando presente."""
    monkeypatch.setattr(cb.shutil, "which", lambda n: "/usr/bin/wl-copy" if n == "wl-copy" else None)
    runs: list[tuple[Any, Any]] = []
    monkeypatch.setattr(cb.subprocess, "run", lambda cmd, **kw: runs.append((cmd, kw.get("input"))))
    WslClipboardWriter().copy("ação")
    assert runs[0][0] == ["wl-copy"]
    assert runs[0][1] == "ação".encode()  # UTF-8 para wl-copy


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
    monkeypatch.setattr(cb, "_interop_works", lambda: True)  # interop funcional → clip.exe é candidato

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
    """clip.exe presente mas com Exec format error (binfmt ausente) → erro instrutivo."""
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
    writer.copy("b")  # 2ª escrita usa o writer cacheado — não re-escaneia
    assert calls["which"] == after_first


def test_wsl_self_heals_when_cached_writer_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se o mecanismo cacheado (wl-copy) começa a falhar numa sessão longa, o
    writer re-resolve e cai para outro — não fica preso no que quebrou."""
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
    writer.copy("primeiro")  # wl-copy funciona e é cacheado
    assert writer._writer_name == "wl-copy"

    state["wl_ok"] = False  # ponte do WSLg cai
    writer.copy("segundo")  # cacheado falha → re-resolve → cai no pyperclip
    assert copied == ["segundo"]
    assert writer._writer_name == "pyperclip"


def test_clip_exe_not_offered_when_interop_broken(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sem o WSLInterop, o clip.exe 'sucede' via /bin/sh sem copiar nada (falso
    positivo). Não pode ser candidato — senão o app acha que copiou e não copiou."""
    fake_clip = tmp_path / "clip.exe"
    fake_clip.write_bytes(b"")
    monkeypatch.setattr(cb.shutil, "which", lambda n: None)  # sem wl-copy/xclip/clip.exe no PATH
    monkeypatch.setattr(cb, "_WINDOWS_CLIP_EXE", fake_clip)
    monkeypatch.setattr(cb, "_interop_works", lambda: False)
    names = [name for name, _ in WslClipboardWriter()._candidates()]
    assert "clip.exe" not in names

    monkeypatch.setattr(cb, "_interop_works", lambda: True)
    names = [name for name, _ in WslClipboardWriter()._candidates()]
    assert "clip.exe" in names


def test_wsl_writer_records_to_session_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """O texto vai para o SessionStatus (p/ o Windows setar o clipboard via /result),
    mesmo que o mecanismo local (wl-copy) funcione ou não."""
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
