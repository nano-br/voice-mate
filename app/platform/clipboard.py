"""Escrita no clipboard por plataforma, atrás de um Protocol injetável.

`pyperclip` cobre Windows, X11 (xclip/xsel) e Wayland (wl-copy). No WSL2 o caso
é mais delicado: o WSLg roda Wayland, então **wl-copy** é o caminho nativo mais
confiável (e sincroniza com o clipboard do Windows). O `clip.exe` via interop é
o último recurso — com `appendWindowsPath=false` ele some do PATH e, se o
binfmt do WSLInterop não estiver registrado (comum com systemd), nem executa
(`Exec format error`). Por isso, no WSL, tentamos os utilitários nativos antes.

`WslClipboardWriter` é AUTO-RECUPERÁVEL: cacheia o mecanismo que funcionou, mas
se ele FALHAR depois (a ponte Wayland do WSLg fica instável em sessões longas),
limpa o cache e re-resolve do zero, caindo para outro mecanismo em vez de ficar
preso no que quebrou. (O caminho mais robusto é o clip.exe — direto ao Windows,
sem a ponte do WSLg —, mas ele exige o interop do WSLInterop registrado.)
"""

from __future__ import annotations

import codecs
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.platform.kinds import PlatformKind

if TYPE_CHECKING:
    from app.core.session_status import SessionStatus

# Caminho padrão do clip.exe via interop (quando appendWindowsPath=false tira do PATH).
_WINDOWS_CLIP_EXE = Path("/mnt/c/Windows/System32/clip.exe")
# Timeout dos utilitários de clipboard: o wl-copy retorna na hora (forka um
# daemon), mas se a ponte Wayland do WSLg travar, sem timeout o app penduraria.
_CLIP_TIMEOUT = 5.0


class ClipboardWriter(Protocol):
    """Escreve texto no clipboard do ambiente do usuário."""

    def copy(self, text: str) -> None: ...


class PyperclipWriter:
    """Clipboard via pyperclip (Windows / X11 / Wayland)."""

    def copy(self, text: str) -> None:
        import pyperclip

        pyperclip.copy(text)


def _wl_copy(text: str) -> None:
    subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True, timeout=_CLIP_TIMEOUT)  # noqa: S603, S607


def _xclip(text: str) -> None:
    subprocess.run(  # noqa: S603, S607
        ["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True, timeout=_CLIP_TIMEOUT
    )


def _pyperclip_copy(text: str) -> None:
    import pyperclip

    pyperclip.copy(text)


def _interop_works() -> bool:
    """True se o WSLInterop está registrado E habilitado (clip.exe pode executar).

    Sem isso, o execve do .exe falha (ENOEXEC) e o `subprocess` do Python cai no
    fallback para `/bin/sh`, que "tem sucesso" (rc=0) SEM copiar nada — um falso
    positivo que faz o app achar que copiou quando não copiou. Por isso o clip.exe
    só é oferecido quando o interop está de fato funcional.
    """
    try:
        return Path("/proc/sys/fs/binfmt_misc/WSLInterop").read_text(encoding="utf-8").startswith("enabled")
    except OSError:
        return False


class WslClipboardWriter:
    """Clipboard no WSL2: wl-copy/xclip (nativos, via WSLg) → pyperclip → clip.exe.

    Tenta as estratégias em ordem, fica com a primeira que funcionar (cacheada).
    O clip.exe lê UTF-16LE com BOM; os demais, UTF-8.
    """

    def __init__(self, status: SessionStatus | None = None) -> None:
        self._writer: Callable[[str], None] | None = None
        self._writer_name = ""
        self._status = status

    def copy(self, text: str) -> None:
        # Publica no hub para o Windows buscar via /result e setar o clipboard
        # nativo (Set-Clipboard) — o caminho confiável no WSL2, onde a ponte do
        # WSLg/interop falha. O wl-copy abaixo é best-effort (clipboard local do WSL).
        if self._status is not None:
            self._status.record_result(text)
        # Caminho rápido: usa o mecanismo já validado. Se ele FALHAR (a ponte
        # Wayland do WSLg fica instável em sessões longas), NÃO desiste — limpa o
        # cache e re-resolve do zero, caindo para outro mecanismo (auto-recupera).
        if self._writer is not None:
            try:
                self._writer(text)
                return
            except Exception as exc:  # noqa: BLE001 — re-resolve abaixo
                print(
                    f"[VoiceMate] ⚠ clipboard '{self._writer_name}' falhou ({exc}); "
                    "re-tentando os outros mecanismos...",
                    file=sys.stderr,
                )
                self._writer = None
                self._writer_name = ""
        errors: list[str] = []
        for name, writer in self._candidates():
            try:
                writer(text)
                self._writer = writer
                self._writer_name = name
                return
            except Exception as exc:  # noqa: BLE001 — tenta a próxima estratégia
                errors.append(f"{name}: {exc}")
        joined = "\n  ".join(errors)
        raise RuntimeError(
            "Nenhum mecanismo de clipboard funcionou no WSL:\n  "
            f"{joined}\n"
            "Instale o utilitário nativo do WSLg: sudo apt install -y wl-clipboard"
        )

    def _candidates(self) -> list[tuple[str, Callable[[str], None]]]:
        out: list[tuple[str, Callable[[str], None]]] = []
        if shutil.which("wl-copy"):
            out.append(("wl-copy", _wl_copy))
        if shutil.which("xclip"):
            out.append(("xclip", _xclip))
        out.append(("pyperclip", _pyperclip_copy))
        # clip.exe SÓ quando o interop está funcional (senão "sucede" sem copiar).
        if _interop_works():
            clip = shutil.which("clip.exe") or (str(_WINDOWS_CLIP_EXE) if _WINDOWS_CLIP_EXE.exists() else None)
            if clip is not None:
                out.append(("clip.exe", self._make_clip_exe_writer(clip)))
        return out

    @staticmethod
    def _make_clip_exe_writer(clip_path: str) -> Callable[[str], None]:
        def _writer(text: str) -> None:
            payload = codecs.BOM_UTF16_LE + text.encode("utf-16-le")
            subprocess.run([clip_path], input=payload, check=True, timeout=_CLIP_TIMEOUT)  # noqa: S603

        return _writer


def create_clipboard_writer(platform: PlatformKind, status: SessionStatus | None = None) -> ClipboardWriter:
    if platform == "wsl2":
        return WslClipboardWriter(status)
    return PyperclipWriter()
