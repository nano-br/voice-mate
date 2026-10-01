"""How many ACTIVE capture endpoints (microphones) Windows has, via Core Audio (MMDevice API).

Port of the hotkeys script's `VoiceMateCoreAudio.GetActiveCaptureCount`, with ctypes COM
calls by vtable slot. Safe from any thread: COM is initialized (MTA) per call.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid
from collections.abc import Callable
from ctypes import wintypes
from typing import Final

assert sys.platform == "win32"  # also tells type checkers this module is Windows-only

log = logging.getLogger(__name__)

_ole32 = ctypes.WinDLL("ole32", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


def _guid(text: str) -> GUID:
    return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_MMDEVICE_ENUMERATOR: Final = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDEVICE_ENUMERATOR: Final = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
CLSCTX_ALL: Final = 0x17
COINIT_MULTITHREADED: Final = 0x0
E_CAPTURE: Final = 1
DEVICE_STATE_ACTIVE: Final = 0x1

_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_ole32.CoInitializeEx.restype = ctypes.c_long
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(GUID),
    ctypes.POINTER(ctypes.c_void_p),
]
_ole32.CoCreateInstance.restype = ctypes.c_long


def _method(interface: ctypes.c_void_p, slot: int, *argtypes: type) -> Callable[..., int]:
    """The COM method at vtable `slot` (0..2 are IUnknown), bound to nothing: pass `interface` first."""
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    function: Callable[..., int] = prototype(vtable[slot])
    return function


def _release(interface: ctypes.c_void_p) -> None:
    if interface:
        _method(interface, 2)(interface)


def active_capture_count() -> int:
    """Number of active microphones, or -1 when the query failed."""
    hr = _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    initialized = hr in (0, 1)  # S_OK / S_FALSE; RPC_E_CHANGED_MODE: COM usable, not ours to undo
    enumerator = ctypes.c_void_p()
    collection = ctypes.c_void_p()
    try:
        hr = _ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDEVICE_ENUMERATOR),
            None,
            CLSCTX_ALL,
            ctypes.byref(IID_IMMDEVICE_ENUMERATOR),
            ctypes.byref(enumerator),
        )
        if hr < 0 or not enumerator:
            return -1
        # IMMDeviceEnumerator::EnumAudioEndpoints(EDataFlow, DWORD, IMMDeviceCollection**)
        enum_endpoints = _method(enumerator, 3, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
        hr = enum_endpoints(enumerator, E_CAPTURE, DEVICE_STATE_ACTIVE, ctypes.byref(collection))
        if hr < 0 or not collection:
            return -1
        count = ctypes.c_uint()
        # IMMDeviceCollection::GetCount(UINT*)
        hr = _method(collection, 3, ctypes.POINTER(ctypes.c_uint))(collection, ctypes.byref(count))
        return -1 if hr < 0 else int(count.value)
    except OSError:
        log.exception("Core Audio query failed")
        return -1
    finally:
        _release(collection)
        _release(enumerator)
        if initialized:
            _ole32.CoUninitialize()
