"""Keep the tray icon on the taskbar on Windows 11, not in the overflow behind the arrow.

Windows 11 puts every NEW notification-area icon in the hidden overflow, so the state
icon (recording, transcribing, ready...) would never be seen. It keeps one subkey per
icon under `HKCU\\Control Panel\\NotifyIconSettings\\<id>`: REG_SZ `ExecutablePath`
and REG_DWORD `IsPromoted` (1 = on the taskbar, 0 or absent = in the overflow).
Explorer creates the subkey asynchronously the first time an icon of an executable is
shown (so the caller retries a few times after the tray icon appears) and applies
`IsPromoted` changes live. Windows 10 has no such key: nothing happens there.

`ExecutablePath` may start with a known-folder GUID instead of the folder itself
(`{6D809377-6AF0-444B-8957-A3773F02200E}\\App\\app.exe` for Program Files): it is
resolved before comparing. Only the entries of THIS process's executable are touched.
Never raises: problems are logged.
"""

from __future__ import annotations

import ctypes
import logging
import ntpath
import re
import sys
import uuid
from collections.abc import Callable, Iterable
from typing import Final, Protocol

log = logging.getLogger(__name__)

NOTIFY_ICON_SETTINGS_KEY: Final = r"Control Panel\NotifyIconSettings"
EXECUTABLE_PATH_VALUE: Final = "ExecutablePath"
IS_PROMOTED_VALUE: Final = "IsPromoted"
_KNOWN_FOLDER_RE: Final = re.compile(r"^(\{[0-9A-Fa-f-]{36}\})(\\.*)?$")


class NotifyIconRegistry(Protocol):
    """The `NotifyIconSettings` key (the real one is winreg; tests inject a fake)."""

    def subkeys(self) -> list[str]:
        """Names of the per-icon subkeys. FileNotFoundError when the key does not exist."""

    def executable_path(self, subkey: str) -> str | None: ...

    def is_promoted(self, subkey: str) -> int | None: ...

    def set_promoted(self, subkey: str, value: int) -> None: ...


class WinregNotifyIconRegistry:
    """`HKCU\\Control Panel\\NotifyIconSettings` through winreg."""

    def __init__(self, key_path: str = NOTIFY_ICON_SETTINGS_KEY) -> None:
        self.key_path = key_path

    def subkeys(self) -> list[str]:
        if sys.platform != "win32":
            raise FileNotFoundError(self.key_path)
        import winreg

        names: list[str] = []
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, index))
                except OSError:  # ERROR_NO_MORE_ITEMS
                    return names
                index += 1

    def _query(self, subkey: str, name: str) -> object:
        if sys.platform != "win32":
            return None
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, f"{self.key_path}\\{subkey}", 0, winreg.KEY_QUERY_VALUE
            ) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        result: object = value
        return result

    def executable_path(self, subkey: str) -> str | None:
        value = self._query(subkey, EXECUTABLE_PATH_VALUE)
        return value if isinstance(value, str) else None

    def is_promoted(self, subkey: str) -> int | None:
        value = self._query(subkey, IS_PROMOTED_VALUE)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def set_promoted(self, subkey: str, value: int) -> None:
        if sys.platform != "win32":
            return
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{self.key_path}\\{subkey}", 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, IS_PROMOTED_VALUE, 0, winreg.REG_DWORD, value)


def known_folder_path(folder_id: str) -> str | None:
    """`SHGetKnownFolderPath` for a `{GUID}`; None when it is not a known folder here."""
    if sys.platform != "win32":
        return None
    try:
        guid = uuid.UUID(folder_id)
    except ValueError:
        return None
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    function = shell32.SHGetKnownFolderPath
    function.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    function.restype = ctypes.c_long  # HRESULT
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    raw = (ctypes.c_ubyte * 16).from_buffer_copy(guid.bytes_le)
    pointer = ctypes.c_void_p()
    result = int(function(ctypes.byref(raw), 0, None, ctypes.byref(pointer)))
    try:
        if result != 0 or not pointer.value:
            return None
        return ctypes.wstring_at(pointer.value)
    finally:
        if pointer.value:
            ole32.CoTaskMemFree(pointer)


def process_image_path() -> str | None:
    """The image of this process. From a venv, `sys.executable` is the venv's launcher while
    the process (the one Explorer records) is the base interpreter; frozen, both match."""
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetModuleFileNameW
    function.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    function.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    length = int(function(None, buffer, len(buffer)))
    return buffer.value if 0 < length < len(buffer) else None


def _canonical(path: str) -> str:
    # ntpath (os.path on Windows): the registry paths are Windows paths on any host.
    return ntpath.normcase(ntpath.abspath(path))


def _expand(path: str, resolve_folder: Callable[[str], str | None]) -> str | None:
    match = _KNOWN_FOLDER_RE.match(path.strip())
    if match is None:
        return path.strip()
    folder = resolve_folder(match.group(1))
    if folder is None:
        return None
    return folder.rstrip("\\") + (match.group(2) or "")


def own_executables() -> tuple[str, ...]:
    """Paths that identify this process in `ExecutablePath`."""
    candidates = [sys.executable]
    image = process_image_path()
    if image:
        candidates.append(image)
    return tuple(path for path in candidates if path)


def set_tray_icon_promoted(
    promoted: bool,
    *,
    executables: Iterable[str] | None = None,
    registry: NotifyIconRegistry | None = None,
    resolve_folder: Callable[[str], str | None] = known_folder_path,
) -> bool:
    """Set `IsPromoted` on the entries of our executable. True when at least one entry of
    ours exists (then the caller stops retrying), False when none exists yet, the key is
    missing (Windows 10) or anything failed (logged, never raised)."""
    try:
        wanted = {_canonical(path) for path in (executables if executables is not None else own_executables())}
        if not wanted:
            return False
        source = registry if registry is not None else WinregNotifyIconRegistry()
        try:
            names = source.subkeys()
        except FileNotFoundError:
            log.debug("no NotifyIconSettings key (not Windows 11): tray icon left as is")
            return False
        value = 1 if promoted else 0
        found = False
        for name in names:
            try:
                path = source.executable_path(name)
                expanded = _expand(path, resolve_folder) if path else None
                if expanded is None or _canonical(expanded) not in wanted:
                    continue
                found = True
                if source.is_promoted(name) != value:
                    source.set_promoted(name, value)
                    log.info("tray icon entry %s: IsPromoted=%s", name, value)
            except OSError as exc:
                log.warning("tray icon entry %s could not be updated: %s", name, exc)
        if not found:
            log.debug("no tray icon entry for %s yet", sorted(wanted))
        return found
    except Exception:  # noqa: BLE001 - a cosmetic setting must never break the app
        log.exception("changing the tray icon visibility failed")
        return False
