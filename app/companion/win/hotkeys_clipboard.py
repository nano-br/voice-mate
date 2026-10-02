"""The companion's Win32 thread: global hotkeys and verified clipboard writes.

RegisterHotKey and OpenClipboard are bound to a thread/window, so ONE dedicated thread
owns a message-only window, every hotkey registration (always MOD_NOREPEAT) and every
clipboard write/read-back. Other threads reach it by posting messages (`_call` waits for
the answer, `deliver` does not).

Clipboard delivery is a port of the hotkeys script's `Set-ClipboardReliable`, run as a
`SetTimer`-driven state machine so hotkeys stay responsive (never `sleep` on this thread):
skip an attempt while another process holds the clipboard open; set, read back, compare
(tolerating a trailing newline and CRLF/LF); up to 5 attempts, 60..300 ms apart. Then, for
the Win+V history (which coalesces fast changes), wait 250 ms and, if we still own the
clipboard (`GetClipboardOwner`), set it once more, verify it, and let it settle 250 ms
before the next delivery. Delivery only reads back our own data: another app's data may
be delay-rendered, and reading it would block this thread.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final, Literal, TypeVar

from app.companion.chords import Chord, parse_chord
from app.companion.contract import HotkeyCheck

assert sys.platform == "win32"  # also tells type checkers this module is Windows-only

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


_user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
_user32.RegisterClassExW.restype = wintypes.ATOM
_user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
_user32.UnregisterClassW.restype = wintypes.BOOL
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.DestroyWindow.argtypes = [wintypes.HWND]
_user32.DestroyWindow.restype = wintypes.BOOL
_user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.DefWindowProcW.restype = LRESULT
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
_user32.GetMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = LRESULT
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_user32.PostQuitMessage.restype = None
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL
_user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
_user32.SetTimer.restype = ctypes.c_size_t
_user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
_user32.KillTimer.restype = wintypes.BOOL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.CountClipboardFormats.argtypes = []
_user32.CountClipboardFormats.restype = ctypes.c_int
_user32.GetClipboardOwner.argtypes = []
_user32.GetClipboardOwner.restype = wintypes.HWND
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL

HWND_MESSAGE: Final = wintypes.HWND(-3)
WM_DESTROY: Final = 0x0002
WM_CLOSE: Final = 0x0010
WM_TIMER: Final = 0x0113
WM_HOTKEY: Final = 0x0312
WM_APP: Final = 0x8000
_WM_CALL: Final = WM_APP + 1
CF_UNICODETEXT: Final = 13
GMEM_MOVEABLE: Final = 0x0002

MOD_ALT: Final = 0x0001
MOD_CONTROL: Final = 0x0002
MOD_SHIFT: Final = 0x0004
MOD_WIN: Final = 0x0008
# Without it, HOLDING the keys repeats WM_HOTKEY and toggles start/stop over and over.
MOD_NOREPEAT: Final = 0x4000
_MOD_FLAGS: Final = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}

_TIMER_CLIPBOARD: Final = 1
_PROBE_ID: Final = 0xBFFF  # ids 0x0000..0xBFFF belong to the application
CLIPBOARD_ATTEMPTS: Final = 5
CLIPBOARD_BACKOFF_MS: Final = 60  # attempt n waits n * 60 ms before the read-back
HISTORY_DELAY_MS: Final = 250
REASSERT_ATTEMPTS: Final = 3
SETTLE_MS: Final = 250


def _virtual_keys() -> dict[str, int]:
    keys: dict[str, int] = {}
    for code in range(ord("a"), ord("z") + 1):
        keys[chr(code)] = ord(chr(code).upper())
    for digit in range(10):
        keys[str(digit)] = ord(str(digit))
        keys[f"num{digit}"] = 0x60 + digit
    for number in range(1, 25):
        keys[f"f{number}"] = 0x70 + number - 1
    keys.update(
        {
            "space": 0x20,
            "enter": 0x0D,
            "tab": 0x09,
            "esc": 0x1B,
            "backspace": 0x08,
            "insert": 0x2D,
            "delete": 0x2E,
            "home": 0x24,
            "end": 0x23,
            "page up": 0x21,
            "page down": 0x22,
            "up": 0x26,
            "down": 0x28,
            "left": 0x25,
            "right": 0x27,
            "pause": 0x13,
            "print screen": 0x2C,
            ";": 0xBA,
            "=": 0xBB,
            ",": 0xBC,
            "-": 0xBD,
            ".": 0xBE,
            "/": 0xBF,
            "`": 0xC0,
            "[": 0xDB,
            "\\": 0xDC,
            "]": 0xDD,
            "'": 0xDE,
        }
    )
    return keys


VIRTUAL_KEYS: Final = _virtual_keys()


def hotkey_args(chord: Chord) -> tuple[int, int]:
    """(fsModifiers incl. MOD_NOREPEAT, vk) for RegisterHotKey."""
    modifiers = MOD_NOREPEAT
    for mod in chord.modifiers:
        modifiers |= _MOD_FLAGS[mod]
    return modifiers, VIRTUAL_KEYS[chord.key]


def same_text(current: str | None, expected: str) -> bool:
    """Read-back comparison, tolerant of a trailing newline and of CRLF vs LF."""
    if current is None:
        return False
    if current == expected:
        return True
    return current.replace("\r\n", "\n").rstrip("\n") == expected.replace("\r\n", "\n").rstrip("\n")


ClipPhase = Literal["verify", "history", "settle"]


@dataclass
class _ClipJob:
    text: str
    done: Callable[[bool], None]
    attempt: int = 0
    phase: ClipPhase = "verify"
    reasserts: int = 0


_T = TypeVar("_T")


class HotkeyClipboardThread:
    """Owns the message-only window. Public methods are thread-safe."""

    def __init__(self, call_timeout_s: float = 3.0) -> None:
        self._call_timeout_s = call_timeout_s
        self._calls: queue.SimpleQueue[tuple[Callable[[], object], Future[object]]] = queue.SimpleQueue()
        self._hwnd: int | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._start_error: str = ""
        self._on_hotkey: Callable[[str], None] = lambda flow: None
        self._wndproc = WNDPROC(self._window_proc)  # keep a reference: Windows calls it
        self._class_name = f"VoiceMateCompanion.{id(self):x}"
        # Thread-confined state (only touched on the Win32 thread).
        self._desired: dict[str, Chord] = {}
        self._registered: dict[int, str] = {}  # hotkey id -> flow
        self._registered_chords: dict[str, Chord] = {}  # flow -> chord actually held
        self._suspended = False
        self._next_id = 1
        self._jobs: deque[_ClipJob] = deque()
        self._current: _ClipJob | None = None

    # --- lifecycle -------------------------------------------------------------------------------

    def start(self, on_hotkey: Callable[[str], None]) -> None:
        """Start the thread; `on_hotkey(flow)` runs ON the Win32 thread: keep it short."""
        if self._thread is not None:
            return
        self._on_hotkey = on_hotkey
        self._thread = threading.Thread(target=self._run, name="companion-win32", daemon=True)
        self._thread.start()
        if not self._ready.wait(5.0) or self._hwnd is None:
            raise OSError(f"the Win32 thread did not start: {self._start_error or 'timeout'}")

    def stop(self, timeout_s: float = 3.0) -> None:
        thread = self._thread
        if thread is None or self._hwnd is None:
            return
        try:
            self._call(self._teardown)
        except (TimeoutError, OSError):
            log.warning("win32 thread did not answer the stop request")
        _user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        thread.join(timeout_s)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- public API (any thread) -------------------------------------------------------------------

    def register(self, bindings: Mapping[str, str], *, atomic: bool = False) -> dict[str, HotkeyCheck]:
        """Replace the whole set (flow -> chord). Per-flow verdicts: ok / in_use / invalid.

        `atomic`: any failure restores the previous registrations (settings apply)."""
        items = dict(bindings)
        return self._call(lambda: self._register(items, atomic))

    def check(self, chord: str) -> HotkeyCheck:
        """Would this chord register? Our own current registration counts as ok."""
        return self._call(lambda: self._check(chord))

    def suspend(self, suspended: bool) -> None:
        """Non-blocking (the settings window calls it from the GUI thread); calls keep their order."""
        self._post(lambda: self._suspend(suspended))

    def registered(self) -> dict[str, str]:
        """flow -> chord currently held."""
        return self._call(lambda: {flow: chord.normalized() for flow, chord in self._registered_chords.items()})

    def register_missing(self) -> dict[str, HotkeyCheck]:
        """Try only the desired chords not held yet (a retry): held ones are never released.
        While suspended it only probes. Verdicts for the missing flows ({} = all held)."""
        return self._call(self._register_missing)

    def deliver(self, text: str, done: Callable[[bool], None]) -> None:
        """Queue a verified write; `done(ok)` runs on the Win32 thread when it is settled
        (or at once, on the caller's thread, when the Win32 thread is not running)."""
        future = self._post(lambda: self._enqueue(_ClipJob(text, done)))
        if future.done() and future.exception() is not None:
            done(False)

    def read_text(self) -> str | None:
        """The clipboard's text ("" when it holds no text; None when another process holds it open)."""
        return self._call(self._read_clipboard)

    def has_non_text_content(self) -> bool:
        def probe() -> bool:
            if not _user32.OpenClipboard(self._hwnd):
                return False
            try:
                count = _user32.CountClipboardFormats()
                return count > 0 and not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT)
            finally:
                _user32.CloseClipboard()

        return self._call(probe)

    def post_hotkey_message(self, hotkey_id: int) -> bool:
        """Test hook: post WM_HOTKEY as Windows would."""
        return bool(self._hwnd is not None and _user32.PostMessageW(self._hwnd, WM_HOTKEY, hotkey_id, 0))

    def hotkey_id(self, flow: str) -> int | None:
        return self._call(lambda: next((key for key, value in self._registered.items() if value == flow), None))

    # --- cross-thread plumbing ------------------------------------------------------------------------

    def _on_own_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def _post(self, fn: Callable[[], object]) -> Future[object]:
        future: Future[object] = Future()
        if self._hwnd is None:
            future.set_exception(OSError("win32 thread not running"))
            return future
        self._calls.put((fn, future))
        if not _user32.PostMessageW(self._hwnd, _WM_CALL, 0, 0):
            future.set_exception(OSError(f"PostMessageW failed ({ctypes.get_last_error()})"))
        return future

    def _call(self, fn: Callable[[], _T]) -> _T:
        if self._on_own_thread():
            return fn()
        future = self._post(fn)
        result = future.result(self._call_timeout_s)
        return result  # type: ignore[return-value]

    def _drain_calls(self) -> None:
        while True:
            try:
                fn, future = self._calls.get_nowait()
            except queue.Empty:
                return
            if future.done():
                continue
            try:
                future.set_result(fn())
            except Exception as exc:  # noqa: BLE001 - handed to the caller
                future.set_exception(exc)

    # --- the thread -------------------------------------------------------------------------------------

    def _run(self) -> None:
        instance = _kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name
        if not _user32.RegisterClassExW(ctypes.byref(window_class)):
            self._start_error = f"RegisterClassExW failed ({ctypes.get_last_error()})"
            self._ready.set()
            return
        hwnd = _user32.CreateWindowExW(
            0, self._class_name, self._class_name, 0, 0, 0, 0, 0, HWND_MESSAGE, None, instance, None
        )
        if not hwnd:
            self._start_error = f"CreateWindowExW failed ({ctypes.get_last_error()})"
            _user32.UnregisterClassW(self._class_name, instance)
            self._ready.set()
            return
        self._hwnd = hwnd
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while True:
                result = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result in (0, -1):
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._teardown()
            self._hwnd = None
            _user32.UnregisterClassW(self._class_name, instance)
            # Fail whatever is still waiting for this thread.
            while True:
                try:
                    _fn, future = self._calls.get_nowait()
                except queue.Empty:
                    break
                if not future.done():
                    future.set_exception(OSError("win32 thread stopped"))

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        try:
            if message == WM_HOTKEY:
                flow = self._registered.get(int(wparam))
                if flow is not None:
                    self._on_hotkey(flow)
                return 0
            if message == WM_TIMER and int(wparam) == _TIMER_CLIPBOARD:
                _user32.KillTimer(hwnd, _TIMER_CLIPBOARD)
                self._on_clipboard_timer()
                return 0
            if message == _WM_CALL:
                self._drain_calls()
                return 0
            if message == WM_CLOSE:
                _user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                _user32.PostQuitMessage(0)
                return 0
        except Exception:  # noqa: BLE001 - an exception must never cross into Windows
            log.exception("win32 window procedure failed (message 0x%04x)", message)
            return 0
        return int(_user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _teardown(self) -> None:
        self._unregister_all()
        if self._hwnd is not None:
            _user32.KillTimer(self._hwnd, _TIMER_CLIPBOARD)
        jobs = ([self._current] if self._current is not None else []) + list(self._jobs)
        self._current = None
        self._jobs.clear()
        for job in jobs:
            self._safe_done(job, False)

    # --- hotkeys (Win32 thread) -----------------------------------------------------------------------

    def _register_one(self, flow: str, chord: Chord) -> HotkeyCheck:
        modifiers, vk = hotkey_args(chord)
        hotkey_id = self._next_id
        self._next_id = self._next_id % (_PROBE_ID - 1) + 1
        if not _user32.RegisterHotKey(self._hwnd, hotkey_id, modifiers, vk):
            log.info("RegisterHotKey(%s) failed: error %s", chord.normalized(), ctypes.get_last_error())
            return "in_use"
        self._registered[hotkey_id] = flow
        self._registered_chords[flow] = chord
        return "ok"

    def _unregister_all(self) -> None:
        for hotkey_id in list(self._registered):
            _user32.UnregisterHotKey(self._hwnd, hotkey_id)
        self._registered.clear()
        self._registered_chords.clear()

    def _probe(self, chord: Chord) -> HotkeyCheck:
        modifiers, vk = hotkey_args(chord)
        if not _user32.RegisterHotKey(self._hwnd, _PROBE_ID, modifiers, vk):
            return "in_use"
        _user32.UnregisterHotKey(self._hwnd, _PROBE_ID)
        return "ok"

    def _register_set(self, chords: Mapping[str, Chord]) -> dict[str, HotkeyCheck]:
        verdicts: dict[str, HotkeyCheck] = {}
        for flow, chord in chords.items():
            verdicts[flow] = self._probe(chord) if self._suspended else self._register_one(flow, chord)
        return verdicts

    def _register_missing(self) -> dict[str, HotkeyCheck]:
        missing = {flow: chord for flow, chord in self._desired.items() if flow not in self._registered_chords}
        return self._register_set(missing)

    def _register(self, bindings: Mapping[str, str], atomic: bool) -> dict[str, HotkeyCheck]:
        parsed = {flow: parse_chord(chord) for flow, chord in bindings.items()}
        verdicts: dict[str, HotkeyCheck] = {flow: "invalid" for flow, chord in parsed.items() if chord is None}
        valid = {flow: chord for flow, chord in parsed.items() if chord is not None}
        if atomic and verdicts:
            return {**{flow: "ok" for flow in valid}, **verdicts}
        previous = dict(self._desired)
        self._unregister_all()
        verdicts.update(self._register_set(valid))
        if atomic and any(verdict != "ok" for verdict in verdicts.values()):
            self._unregister_all()
            self._desired = previous
            if not self._suspended:
                self._register_set(previous)
            return verdicts
        self._desired = valid
        return verdicts

    def _check(self, chord_text: str) -> HotkeyCheck:
        chord = parse_chord(chord_text)
        if chord is None:
            return "invalid"
        own = self._desired.values() if self._suspended else self._registered_chords.values()
        if chord in own:
            return "ok"
        return self._probe(chord)

    def _suspend(self, suspended: bool) -> None:
        if suspended == self._suspended:
            return
        self._suspended = suspended
        if suspended:
            self._unregister_all()
        else:
            self._register_set(self._desired)

    # --- clipboard (Win32 thread) ----------------------------------------------------------------------

    def _write_clipboard(self, text: str) -> bool:
        if not _user32.OpenClipboard(self._hwnd):
            return False  # held by another process: skip this attempt, never block
        try:
            if not _user32.EmptyClipboard():
                return False
            data = text.encode("utf-16-le") + b"\x00\x00"
            handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                _kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(pointer, data, len(data))
            _kernel32.GlobalUnlock(handle)
            if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
                _kernel32.GlobalFree(handle)  # still ours: the system did not take it
                return False
            return True
        finally:
            _user32.CloseClipboard()

    def _read_clipboard(self) -> str | None:
        if not _user32.OpenClipboard(self._hwnd):
            return None
        try:
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()

    def _enqueue(self, job: _ClipJob) -> None:
        self._jobs.append(job)
        if self._current is None:
            self._next_job()

    def _next_job(self) -> None:
        if self._current is not None or not self._jobs:
            return
        self._current = self._jobs.popleft()
        self._attempt()

    def _set_timer(self, delay_ms: int) -> None:
        if not _user32.SetTimer(self._hwnd, _TIMER_CLIPBOARD, delay_ms, None):
            log.warning("SetTimer failed (%s)", ctypes.get_last_error())
            self._finish(False)

    def _attempt(self) -> None:
        job = self._current
        if job is None:
            return
        job.attempt += 1
        job.phase = "verify"
        self._write_clipboard(job.text)
        self._set_timer(CLIPBOARD_BACKOFF_MS * job.attempt)

    def _owned_by_us(self) -> bool:
        owner = _user32.GetClipboardOwner()
        return bool(owner) and owner == self._hwnd

    def _read_own_text(self) -> str | None:
        """Our own data only. Another app's data may be delay-rendered: GetClipboardData on it
        sends WM_RENDERFORMAT to that app and can block this thread."""
        return self._read_clipboard() if self._owned_by_us() else None

    def _on_clipboard_timer(self) -> None:
        job = self._current
        if job is None:
            return
        if job.phase == "verify":
            if same_text(self._read_own_text(), job.text):
                job.phase = "history"
                self._set_timer(HISTORY_DELAY_MS)
            elif job.attempt < CLIPBOARD_ATTEMPTS:
                self._attempt()
            else:
                self._finish(False)
        elif job.phase == "history":
            # Re-assert only while it is still ours: the user may have copied something meanwhile.
            if not self._owned_by_us():
                self._finish(True)
            elif self._write_clipboard(job.text):
                job.phase = "settle"
                self._set_timer(SETTLE_MS)
            elif job.reasserts < REASSERT_ATTEMPTS:
                job.reasserts += 1  # held by another process for an instant: try again shortly
                self._set_timer(CLIPBOARD_BACKOFF_MS)
            else:
                log.warning("clipboard re-assert for the Win+V history failed; the text was delivered once")
                self._finish(True)
        else:
            # Verify the re-assert while it is still ours (a later copy by the user is fine).
            if self._owned_by_us() and not same_text(self._read_clipboard(), job.text):
                log.warning("clipboard re-assert could not be verified")
            self._finish(True)

    def _finish(self, ok: bool) -> None:
        job = self._current
        self._current = None
        if job is not None:
            self._safe_done(job, ok)
        self._next_job()

    @staticmethod
    def _safe_done(job: _ClipJob, ok: bool) -> None:
        try:
            job.done(ok)
        except Exception:  # noqa: BLE001 - a listener must not break the delivery loop
            log.exception("clipboard delivery callback failed")
