"""Single-instance lock on Windows: a named mutex.

`QLocalServer.listen()` succeeds twice on Windows (named pipes allow several
server instances), so it cannot be the lock; it only forwards `--command` to the
running instance. The mutex name is per session ("Local\\"), and Inno Setup's
`AppMutex` checks the same name without that prefix.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from app.companion.contract import SINGLE_INSTANCE_MUTEX

ERROR_ALREADY_EXISTS = 183


class NamedMutex:
    """`CreateMutexW` + `ERROR_ALREADY_EXISTS`: only the first process acquires it.

    The handle stays open while this process runs (Windows closes it on exit, also on
    a crash, so a stale lock is impossible). Not inheritable: child processes such
    as `wsl.exe` never keep it alive.
    """

    def __init__(self, name: str = SINGLE_INSTANCE_MUTEX) -> None:
        self.name = name
        self._handle: int | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """True when this process now owns the name; False when another instance does."""
        if self._handle is not None:
            return True
        if sys.platform != "win32":
            raise OSError("NamedMutex is Windows-only")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateMutexW
        create.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
        handle = create(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == ERROR_ALREADY_EXISTS:
            _close_handle(handle)
            return False
        self._handle = int(handle)
        return True

    def release(self) -> None:
        if self._handle is not None:
            _close_handle(self._handle)
            self._handle = None


def _close_handle(handle: int) -> None:
    if sys.platform != "win32":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    close(handle)
