"""Escrita no clipboard por plataforma, atrás de um Protocol injetável.

`pyperclip` já cobre Windows, X11 (xclip/xsel) e Wayland (wl-copy). No WSL2 o
clipboard do WSLg normalmente sincroniza com o do Windows pelos mesmos
mecanismos; quando as ferramentas Linux não estão instaladas, o fallback é o
`clip.exe` do Windows via interop (write-only — suficiente: o app só escreve).
"""

from __future__ import annotations

import codecs
import subprocess
import sys
from typing import Protocol

from app.platform.kinds import PlatformKind


class ClipboardWriter(Protocol):
    """Escreve texto no clipboard do ambiente do usuário."""

    def copy(self, text: str) -> None: ...


class PyperclipWriter:
    """Clipboard via pyperclip (Windows / X11 / Wayland)."""

    def copy(self, text: str) -> None:
        import pyperclip

        pyperclip.copy(text)


class WslClipboardWriter:
    """Clipboard no WSL2: tenta pyperclip (WSLg) e cai para clip.exe do Windows.

    O clip.exe lê UTF-16LE com BOM do stdin — é o que garante acentuação
    correta em PT-BR independente da codepage do console.
    """

    def __init__(self) -> None:
        self._pyperclip_ok: bool | None = None

    def copy(self, text: str) -> None:
        if self._pyperclip_ok is not False:
            try:
                import pyperclip

                pyperclip.copy(text)
                self._pyperclip_ok = True
                return
            except Exception as exc:  # noqa: BLE001 — sem xclip/wl-copy no WSL: usar interop
                if self._pyperclip_ok is None:
                    print(
                        f"[VoiceMate] pyperclip indisponível no WSL ({exc}); usando clip.exe.",
                        file=sys.stderr,
                    )
                self._pyperclip_ok = False
        self._copy_via_clip_exe(text)

    @staticmethod
    def _copy_via_clip_exe(text: str) -> None:
        payload = codecs.BOM_UTF16_LE + text.encode("utf-16-le")
        subprocess.run(["clip.exe"], input=payload, check=True)  # noqa: S603, S607


def create_clipboard_writer(platform: PlatformKind) -> ClipboardWriter:
    if platform == "wsl2":
        return WslClipboardWriter()
    return PyperclipWriter()
