"""Taskbar identity of the process (AppUserModelID).

Windows groups taskbar buttons, jump lists and pinned shortcuts by AUMID. The
process must set it BEFORE it creates any window (so before `QApplication`), and
the Inno Setup shortcut must carry the same value, or a click on the pin opens a
second taskbar button next to it.
"""

from __future__ import annotations

import ctypes
import logging
import sys

from app.companion.contract import APP_USER_MODEL_ID

log = logging.getLogger(__name__)


def set_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """`SetCurrentProcessExplicitAppUserModelID`; False (logged) when Windows refuses it."""
    if sys.platform != "win32":
        return False
    shell32 = ctypes.WinDLL("shell32")
    function = shell32.SetCurrentProcessExplicitAppUserModelID
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_long  # HRESULT
    result = int(function(app_id))
    if result != 0:
        log.warning("SetCurrentProcessExplicitAppUserModelID(%r) failed: HRESULT 0x%08X", app_id, result & 0xFFFFFFFF)
        return False
    return True
