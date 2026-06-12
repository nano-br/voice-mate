"""Escrita no clipboard por plataforma, atrás de um Protocol injetável.

`pyperclip` já cobre Windows, X11 (xclip/xsel) e Wayland (wl-copy). No WSL2 o
clipboard do WSLg normalmente sincroniza com o do Windows pelos mesmos
mecanismos; quando as ferramentas Linux não estão instaladas, o fallback é o
`clip.exe` do Windows via interop (write-only — suficiente: o app só escreve).
"""

from __future__ import annotations

import codecs
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from app.platform.kinds import PlatformKind

# Caminho padrão do clip.exe via interop. Necessário quando o WSL roda com
# `appendWindowsPath=false` no /etc/wsl.conf (recomendado p/ performance):
# nesse caso clip.exe não está no PATH — nem para nós, nem para o pyperclip.
_WINDOWS_CLIP_EXE = Path("/mnt/c/Windows/System32/clip.exe")


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
        self._clip_exe: str | None = None

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
                        f"[VoiceMate] pyperclip indisponível no WSL ({exc}); usando clip.exe via interop.",
                        file=sys.stderr,
                    )
                self._pyperclip_ok = False
        self._copy_via_clip_exe(text)

    def _resolve_clip_exe(self) -> str:
        """Acha o clip.exe mesmo sem o PATH do Windows (appendWindowsPath=false)."""
        if self._clip_exe is None:
            found = shutil.which("clip.exe")
            if found is None and _WINDOWS_CLIP_EXE.exists():
                found = str(_WINDOWS_CLIP_EXE)
            if found is None:
                raise RuntimeError(
                    "clip.exe não encontrado — habilite o interop do Windows em /etc/wsl.conf "
                    "([interop] enabled=true) ou instale um utilitário de clipboard no WSL "
                    "(sudo apt install -y wl-clipboard)."
                )
            self._clip_exe = found
        return self._clip_exe

    def _copy_via_clip_exe(self, text: str) -> None:
        payload = codecs.BOM_UTF16_LE + text.encode("utf-16-le")
        subprocess.run([self._resolve_clip_exe()], input=payload, check=True)  # noqa: S603


def create_clipboard_writer(platform: PlatformKind) -> ClipboardWriter:
    if platform == "wsl2":
        return WslClipboardWriter()
    return PyperclipWriter()
