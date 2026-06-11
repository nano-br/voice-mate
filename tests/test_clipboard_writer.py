from __future__ import annotations

import subprocess
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

    def _boom(_text: str) -> None:
        raise RuntimeError("sem xclip")

    runs: list[tuple[Any, ...]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> None:  # noqa: ANN401
        runs.append((cmd, kwargs.get("input")))

    monkeypatch.setattr(pyperclip, "copy", _boom)
    monkeypatch.setattr(subprocess, "run", _fake_run)
    writer = WslClipboardWriter()
    writer.copy("ação")  # acentos: precisa sair como UTF-16LE com BOM
    writer.copy("segunda")  # segunda chamada nem tenta pyperclip de novo

    assert len(runs) == 2
    cmd, payload = runs[0]
    assert cmd == ["clip.exe"]
    assert isinstance(payload, bytes)
    assert payload.startswith(b"\xff\xfe")  # BOM UTF-16LE
    assert "ação".encode("utf-16-le") in payload


def test_factory_by_platform() -> None:
    assert isinstance(create_clipboard_writer("windows"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("linux-x11"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("linux-wayland"), PyperclipWriter)
    assert isinstance(create_clipboard_writer("wsl2"), WslClipboardWriter)
