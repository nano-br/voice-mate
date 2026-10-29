"""Streamed PCM playback on Windows with the waveOut API (winmm, through ctypes).

The assistant's voice arrives as signed 16-bit PCM chunks while it is synthesized
(app/companion/voice.py). Qt's QAudioSink would do, but QtMultimedia is not part of
PySide6-Essentials (the companion's only Qt package), and the Addons package it lives in is
hundreds of megabytes. winmm ships with every Windows, needs nothing installed, plays queued
buffers back to back (no gap between chunks) and `waveOutReset` silences at once.

Buffers are polled for completion (no callback thread): `pending()` reclaims the finished ones.
Small chunks are held back until about 100 ms are queued, so playback never starts on a few
milliseconds of audio and then runs dry (`commit` sends the rest at the end of a stream).
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Final

WAVE_MAPPER: Final = 0xFFFFFFFF
WAVE_FORMAT_PCM: Final = 1
CALLBACK_NULL: Final = 0
WHDR_DONE: Final = 0x00000001
MMSYSERR_NOERROR: Final = 0
WAVERR_STILLPLAYING: Final = 33
CLOSE_TRIES: Final = 5
MIN_BLOCK_S: Final = 0.1


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [  # noqa: RUF012 (ctypes layout)
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [  # noqa: RUF012 (ctypes layout)
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


def _winmm() -> ctypes.WinDLL:  # type: ignore[name-defined]
    dll = ctypes.WinDLL("winmm")  # type: ignore[attr-defined]
    handle = ctypes.POINTER(ctypes.c_void_p)
    dll.waveOutOpen.argtypes = [
        handle,
        wintypes.UINT,
        ctypes.POINTER(WAVEFORMATEX),
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    for name in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
        getattr(dll, name).argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    for name in ("waveOutReset", "waveOutClose"):
        getattr(dll, name).argtypes = [ctypes.c_void_p]
    for name in ("waveOutOpen", "waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite", "waveOutReset"):
        getattr(dll, name).restype = wintypes.UINT
    dll.waveOutClose.restype = wintypes.UINT
    return dll


class WaveOutput:
    """A `PcmOutput` (app/companion/voice.py) on the default playback device."""

    def __init__(self) -> None:
        self._dll = _winmm()
        self._lock = threading.Lock()
        self._handle = ctypes.c_void_p()
        self._open = False
        self._buffers: list[tuple[WAVEHDR, ctypes.Array[ctypes.c_char]]] = []
        self._held = bytearray()
        self._block = 0
        # Buffers of a device that refused to close while they were still queued: the driver may
        # read them until the process ends, so they are never freed.
        self._stranded: list[tuple[WAVEHDR, ctypes.Array[ctypes.c_char]]] = []

    def open(self, sample_rate: int, channels: int) -> None:
        fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, channels, sample_rate, sample_rate * channels * 2, channels * 2, 16, 0)
        with self._lock:
            result = self._dll.waveOutOpen(
                ctypes.byref(self._handle), WAVE_MAPPER, ctypes.byref(fmt), 0, 0, CALLBACK_NULL
            )
            if result != MMSYSERR_NOERROR:
                raise OSError(f"waveOutOpen failed ({result})")
            self._open = True
            self._block = int(sample_rate * MIN_BLOCK_S) * channels * 2

    def write(self, data: bytes) -> None:
        with self._lock:
            if not self._open:
                return
            self._held += data
            if len(self._held) >= self._block:
                self._submit()

    def commit(self) -> None:
        with self._lock:
            if self._open and self._held:
                self._submit()

    def pending(self) -> bool:
        with self._lock:
            self._reclaim()
            return bool(self._buffers) or bool(self._held)

    def flush(self) -> None:
        with self._lock:
            self._held.clear()
            if self._open:
                self._dll.waveOutReset(self._handle)  # marks every queued buffer done
            self._reclaim()

    def close(self) -> None:
        with self._lock:
            if not self._open:
                return
            self._held.clear()
            for _ in range(CLOSE_TRIES):
                self._dll.waveOutReset(self._handle)
                self._reclaim()
                if self._dll.waveOutClose(self._handle) != WAVERR_STILLPLAYING:
                    break
                time.sleep(0.01)
            self._open = False
            # Still queued after every try: keep the memory alive instead of freeing it under the driver.
            self._stranded.extend(self._buffers)
            self._buffers = []

    def _submit(self) -> None:
        data = bytes(self._held)
        self._held.clear()
        buffer = ctypes.create_string_buffer(data, len(data))
        header = WAVEHDR(ctypes.cast(buffer, ctypes.c_void_p), len(data), 0, 0, 0, 0, None, 0)
        size = ctypes.sizeof(WAVEHDR)
        if self._dll.waveOutPrepareHeader(self._handle, ctypes.byref(header), size) != MMSYSERR_NOERROR:
            raise OSError("waveOutPrepareHeader failed")
        if self._dll.waveOutWrite(self._handle, ctypes.byref(header), size) != MMSYSERR_NOERROR:
            self._dll.waveOutUnprepareHeader(self._handle, ctypes.byref(header), size)
            raise OSError("waveOutWrite failed")
        self._buffers.append((header, buffer))
        self._reclaim()

    def _reclaim(self) -> None:
        size = ctypes.sizeof(WAVEHDR)
        keep = []
        for header, buffer in self._buffers:
            if header.dwFlags & WHDR_DONE:
                self._dll.waveOutUnprepareHeader(self._handle, ctypes.byref(header), size)
            else:
                keep.append((header, buffer))
        self._buffers = keep
