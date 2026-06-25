"""Per-platform clipboard writing, behind an injectable Protocol.

`pyperclip` covers Windows, X11 (xclip/xsel) and Wayland (wl-copy). On WSL2 the
case is trickier: WSLg runs Wayland, so **wl-copy** is the most reliable native
path (and it syncs with the Windows clipboard). `clip.exe` via interop is the
last resort — with `appendWindowsPath=false` it drops off the PATH, and if the
WSLInterop binfmt is not registered (common with systemd) it won't even run
(`Exec format error`). That's why, on WSL, we try the native utilities first.

`WslClipboardWriter` is SELF-HEALING: it caches the mechanism that worked, but
if that one later FAILS (the WSLg Wayland bridge gets flaky over long sessions),
it clears the cache and re-resolves from scratch, falling back to another
mechanism instead of staying stuck on the broken one. (The most robust path is
clip.exe — straight to Windows, without the WSLg bridge — but it requires the
WSLInterop interop to be registered.)
"""

from __future__ import annotations

import codecs
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.i18n import _
from app.platform.kinds import PlatformKind

if TYPE_CHECKING:
    from app.core.session_status import SessionStatus

# Default clip.exe path via interop (for when appendWindowsPath=false drops it from PATH).
_WINDOWS_CLIP_EXE = Path("/mnt/c/Windows/System32/clip.exe")
# Clipboard-utility timeout: wl-copy returns immediately (forks a daemon), but if
# the WSLg Wayland bridge hangs, without a timeout the app would hang too.
_CLIP_TIMEOUT = 5.0


class ClipboardWriter(Protocol):
    """Writes text to the user's environment clipboard."""

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
    """True if WSLInterop is registered AND enabled (clip.exe can execute).

    Without it, the .exe's execve fails (ENOEXEC) and Python's `subprocess` falls
    back to `/bin/sh`, which "succeeds" (rc=0) WITHOUT copying anything — a false
    positive that makes the app think it copied when it didn't. That's why
    clip.exe is only offered when interop is actually functional.
    """
    try:
        return Path("/proc/sys/fs/binfmt_misc/WSLInterop").read_text(encoding="utf-8").startswith("enabled")
    except OSError:
        return False


class WslClipboardWriter:
    """Clipboard on WSL2: wl-copy/xclip (native, via WSLg) → pyperclip → clip.exe.

    Tries the strategies in order, keeping the first one that works (cached).
    clip.exe reads UTF-16LE with a BOM; the others, UTF-8.
    """

    def __init__(self, status: SessionStatus | None = None) -> None:
        self._writer: Callable[[str], None] | None = None
        self._writer_name = ""
        self._status = status

    def copy(self, text: str) -> None:
        # Publish to the hub so Windows can fetch it via /result and set the
        # native clipboard (Set-Clipboard) — the reliable path on WSL2, where the
        # WSLg/interop bridge fails. The wl-copy below is best-effort (WSL-local clipboard).
        if self._status is not None:
            self._status.record_result(text)
        # Fast path: use the already-validated mechanism. If it FAILS (the WSLg
        # Wayland bridge gets flaky over long sessions), DON'T give up — clear the
        # cache and re-resolve from scratch, falling back to another mechanism (self-heals).
        if self._writer is not None:
            try:
                self._writer(text)
                return
            except Exception as exc:  # noqa: BLE001 — re-resolve below
                print(
                    _(
                        "[VoiceMate] ⚠ clipboard '{writer_name}' failed ({exc}); retrying the other mechanisms..."
                    ).format(writer_name=self._writer_name, exc=exc),
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
            except Exception as exc:  # noqa: BLE001 — try the next strategy
                errors.append(f"{name}: {exc}")
        joined = "\n  ".join(errors)
        raise RuntimeError(
            "No clipboard mechanism worked on WSL:\n  "
            f"{joined}\n"
            "Install the native WSLg utility: sudo apt install -y wl-clipboard"
        )

    def _candidates(self) -> list[tuple[str, Callable[[str], None]]]:
        out: list[tuple[str, Callable[[str], None]]] = []
        if shutil.which("wl-copy"):
            out.append(("wl-copy", _wl_copy))
        if shutil.which("xclip"):
            out.append(("xclip", _xclip))
        out.append(("pyperclip", _pyperclip_copy))
        # clip.exe ONLY when interop is functional (otherwise it "succeeds" without copying).
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
