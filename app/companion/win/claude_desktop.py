"""The Claude Desktop window as a port: find it, list and open conversations, read it, type into it.

Productized form of `tools/lab/claude_client/uia_common.py` and `desktop_background_lab.py`
(docs/research/claude-code-client.md, sections 5 and 6). What the labs established:

- Reading the accessibility tree and invoking sidebar rows work with the window minimized and
  without focus. Typing does not: a "flash" brings the window forward, focuses the ProseMirror
  prompt, pastes through the clipboard, presses Enter and gives the foreground back (about 2 s).
- Every key is its own press and goes through three guards, checked again right before it: the Claude window owns
  the foreground, the open conversation is exactly `<title> - Claude Code`, and the keyboard focus
  is in the prompt editor. A failed guard raises a typed error and nothing is typed.
- The clipboard carries the text: the previous content is saved (every format held in global
  memory) and restored, and the pasted text is kept out of the Win+V history and the cloud clipboard.
  The clipboard sequence number is checked before the paste and before Enter (another program that
  copied meanwhile stops the flash), and the old content is restored only while the clipboard still
  holds ours, so a copy the person made during the flash is never overwritten.
- `send` raises only `DesktopError` subclasses: an OSError (the clipboard held by another program,
  a failed Win32 call) becomes `DesktopBusy`, anything else from UI Automation (COMError when the
  window reloads or closes) becomes `DesktopUnavailable`.

What the driver looks for (window, sidebar rows, conversation document, prompt editor), the key
sequences and every wait come from a `DesktopProfile` (`desktop_profile.py`, a JSON file that can be
updated without a release). A profile is data: the guards below stay in code. The window must belong
to the Claude Desktop process (checked before the profile's matcher sees the window), only the keys
in `desktop_profile.ALLOWED_KEYS` can be sent, and a key is sent only while the Claude window owns
the foreground.

`DesktopSender.send(title, text)` is the whole write API; everything else is read-only. One lock:
one flash at a time. The UI Automation library (`uiautomation`) and the Win32 calls load lazily, so
this module imports on any platform and in the tests (which drive a fake `DesktopBackend` and a fake
`Clipboard`). Standard library only apart from `uiautomation`.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import importlib
import logging
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import PureWindowsPath
from types import SimpleNamespace
from typing import Any, Final, Literal, Protocol

from app.companion.win.desktop_profile import (
    ALLOWED_SENDKEYS,
    DesktopProfile,
    Locator,
    Step,
    load_packaged,
)

log = logging.getLogger(__name__)

# A UI Automation control or window (a `uiautomation` object, opaque to the sender; a fake in the tests).
Control = Any

LOCK_WAIT_S: Final = 30.0  # the packaged profile's timeouts.lock_wait_s
MAX_CLIPBOARD_BYTES: Final = 32 * 1024 * 1024

DesktopErrorCode = Literal[
    "window_missing", "conversation_missing", "ambiguous", "focus_lost", "locked", "busy", "unavailable"
]


class DesktopError(Exception):
    """Base of the typed failures; `code` is stable, `detail` is English text for the log."""

    code: DesktopErrorCode = "unavailable"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or self.code)
        self.detail = detail or self.code


class WindowMissing(DesktopError):
    code = "window_missing"


class ConversationMissing(DesktopError):
    code = "conversation_missing"


class AmbiguousConversation(DesktopError):
    code = "ambiguous"


class FocusLost(DesktopError):
    code = "focus_lost"


class ScreenLocked(DesktopError):
    code = "locked"


class DesktopBusy(DesktopError):
    """Another flash is in progress and did not end in time."""

    code = "busy"


class DesktopUnavailable(DesktopError):
    """Not Windows, or the `uiautomation` package is missing."""

    code = "unavailable"


@dataclass(frozen=True)
class SidebarEntry:
    """A conversation row of the sidebar. `status` is the row's own label ("Running"), localized."""

    title: str
    status: str = ""
    control: Control = field(default=None, compare=False, repr=False)


class ConversationSender(Protocol):
    """What the VoiceMate tools and the desktop port inject: write one prompt to a Desktop conversation."""

    def send(self, title: str, text: str) -> None:
        """Raises `DesktopError` (a subclass) and types nothing when a guard fails."""


class DesktopBackend(Protocol):
    """The UI Automation and Win32 operations the sender needs (a fake in the tests).

    `window` is whatever `find_window` returned. All calls of one sender operation happen inside
    `session()` on one thread, because UI Automation objects must not cross threads.
    """

    def session(self) -> AbstractContextManager[object]: ...

    def is_locked(self) -> bool: ...

    def find_window(self) -> Control | None: ...

    def is_minimized(self, window: Control) -> bool: ...

    def set_minimized(self, window: Control) -> None:
        """Minimize without activating."""

    def activate(self, window: Control) -> None:
        """Restore when minimized and ask Windows for the foreground (may be refused)."""

    def is_foreground(self, window: Control) -> bool: ...

    def foreground(self) -> int:
        """Handle of the foreground window (0 when none)."""

    def restore_foreground(self, handle: int) -> None: ...

    def sidebar_entries(self, window: Control) -> list[SidebarEntry]: ...

    def open_entry(self, entry: SidebarEntry) -> None: ...

    def document_name(self, window: Control) -> str:
        """Name of the open conversation's document ("" when none)."""

    def focus_prompt(self, window: Control) -> bool:
        """Put the keyboard focus in the prompt editor; False when there is no editor."""

    def focus_in_prompt(self) -> bool: ...

    def prompt_text(self, window: Control) -> str: ...

    def press(self, keys: str) -> None: ...

    def visible_text(self, window: Control) -> list[str]: ...


class Clipboard(Protocol):
    def snapshot(self) -> object:
        """Everything that can be saved and restored."""

    def set_text(self, text: str) -> None:
        """Replace the content with `text`, kept out of the clipboard history."""

    def restore(self, snapshot: object) -> None: ...

    def sequence(self) -> int:
        """A number that changes whenever any program changes the clipboard."""


# --- the sender ------------------------------------------------------------------------------

# A profile, or a function returning the current one (`ProfileManager.current`), or None for the packaged one.
ProfileSource = DesktopProfile | Callable[[], DesktopProfile] | None


class DesktopSender:
    """Reads the Claude Desktop through UI Automation and writes to it by flash typing."""

    def __init__(
        self,
        backend: DesktopBackend | None = None,
        clipboard: Clipboard | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        lock_wait_s: float | None = None,
        profile: ProfileSource = None,
    ) -> None:
        self._profile_source = profile
        self._tls = threading.local()  # the profile one operation runs with (it does not change midway)
        self._backend = backend if backend is not None else UiaBackend(self._operation_profile)
        self._clipboard = clipboard if clipboard is not None else Win32Clipboard()
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock_wait_s = lock_wait_s
        self._lock = threading.Lock()

    def _resolve(self) -> DesktopProfile:
        return _resolve_profile(self._profile_source)

    def _operation_profile(self) -> DesktopProfile:
        return getattr(self._tls, "profile", None) or self._resolve()

    def _begin(self) -> DesktopProfile:
        """Snapshot the profile for one public operation (an update applies from the next one)."""
        profile = self._resolve()
        self._tls.profile = profile
        return profile

    # --- reading (works minimized, no focus, no lock needed beyond COM) ------------------------

    def list_titles(self) -> list[str]:
        """Titles of the conversations in the sidebar, in order (may repeat)."""
        self._begin()
        with self._backend.session():
            return [entry.title for entry in self._backend.sidebar_entries(self._require_window())]

    def current_title(self) -> str:
        """Title of the open conversation ("" when none is open)."""
        profile = self._begin()
        with self._backend.session():
            return profile.conversation.title_of(self._backend.document_name(self._require_window()))

    def open_conversation(self, title: str) -> None:
        """Make `title` the open conversation, without activating the window."""
        profile = self._begin()
        with self._backend.session():
            self._open(profile, self._require_window(), title)

    def read_visible(self, title: str | None = None) -> list[str]:
        """The text lines on screen in the open conversation (opens `title` first when given)."""
        profile = self._begin()
        with self._backend.session():
            window = self._require_window()
            if title is not None:
                self._open(profile, window, title)
            return self._backend.visible_text(window)[-profile.conversation.max_visible_lines :]

    # --- writing -------------------------------------------------------------------------------

    def send(self, title: str, text: str) -> None:
        """Type `text` into the conversation called exactly `title` and press Enter."""
        text = text.strip("\r\n")
        if not title.strip() or not text.strip():
            raise ValueError("title and text must not be empty")
        profile = self._begin()
        lock_wait = self._lock_wait_s if self._lock_wait_s is not None else profile.timeouts.lock_wait_s
        if not self._lock.acquire(timeout=lock_wait):
            raise DesktopBusy("another message is being typed into the Claude Desktop")
        try:
            with self._backend.session():
                self._flash(profile, title, text)
        except DesktopError:
            raise
        except OSError as exc:
            raise DesktopBusy(f"the clipboard or the window could not be used ({exc})") from exc
        except Exception as exc:  # COMError and friends from UI Automation: the contract is DesktopError
            raise DesktopUnavailable(f"UI Automation failed ({type(exc).__name__}: {exc})") from exc
        finally:
            self._lock.release()

    # --- internals -----------------------------------------------------------------------------

    def _require_window(self) -> Control:
        if self._backend.is_locked():
            raise ScreenLocked("the screen is locked")
        window = self._backend.find_window()
        if window is None:
            raise WindowMissing("the Claude Desktop window was not found")
        return window

    def _wait(self, profile: DesktopProfile, condition: Callable[[], bool], timeout: float) -> bool:
        deadline = self._monotonic() + timeout
        while True:
            if condition():
                return True
            if self._monotonic() >= deadline:
                return False
            self._sleep(profile.timeouts.poll_s)

    def _open(self, profile: DesktopProfile, window: Control, title: str) -> None:
        """Invoke the sidebar row of `title` (a no-op when it is open already). Types nothing."""
        conversation = profile.conversation
        expected = conversation.document_name(title)
        if self._backend.document_name(window) == expected:
            return
        matches = [entry for entry in self._backend.sidebar_entries(window) if entry.title == title]
        if not matches:
            raise ConversationMissing(f"no conversation titled {title!r} in the sidebar")
        if len(matches) > 1:
            raise AmbiguousConversation(f"{len(matches)} conversations are titled {title!r}")
        self._backend.open_entry(matches[0])
        opened = self._wait(
            profile, lambda: self._backend.document_name(window) == expected, profile.timeouts.open_wait_s
        )
        if not opened:
            shown = conversation.title_of(self._backend.document_name(window))
            raise ConversationMissing(f"opening {title!r} showed {shown!r} instead")

    def _guard(self, profile: DesktopProfile, window: Control, title: str, *, focus: bool = True) -> None:
        """Right before keys: the Claude window is in front, the right conversation is open and
        (when `focus`) the keyboard focus is in the prompt. Raises instead of letting a key land elsewhere."""
        timeouts = profile.timeouts
        if not self._wait(profile, lambda: self._backend.is_foreground(window), timeouts.foreground_wait_s):
            raise FocusLost("the Claude window is not in the foreground")
        name = self._backend.document_name(window)
        if name != profile.conversation.document_name(title):
            raise ConversationMissing(
                f"the open conversation is {profile.conversation.title_of(name)!r}, expected {title!r}"
            )
        if focus and not self._wait(profile, self._backend.focus_in_prompt, timeouts.focus_wait_s):
            raise FocusLost("the keyboard focus is not in the prompt box")

    def _flash(self, profile: DesktopProfile, title: str, text: str) -> None:
        backend = self._backend
        timeouts = profile.timeouts
        window = self._require_window()
        was_minimized = backend.is_minimized(window)
        previous = backend.foreground()
        started = self._monotonic()
        try:
            self._open(profile, window, title)  # background: works minimized, no focus needed
            backend.activate(window)
            self._guard(profile, window, title, focus=False)
            if not self._wait(profile, lambda: backend.focus_prompt(window), timeouts.focus_prompt_wait_s):
                raise FocusLost("the prompt box was not found or did not take the focus")
            saved = self._clipboard.snapshot()
            ours: int | None = None  # the sequence number right after our text went on the clipboard
            try:
                self._press_role(profile, window, title, "clear_draft")
                self._clipboard.set_text(text)
                ours = self._clipboard.sequence()
                still_ours = ours
                self._press_role(
                    profile, window, title, "paste", lambda: self._check_clipboard(still_ours, "before the paste")
                )
                self._wait_pasted(profile, window, text)
                self._press_role(
                    profile, window, title, "submit", lambda: self._check_clipboard(still_ours, "before Enter")
                )
                self._wait(profile, lambda: not backend.prompt_text(window).strip(), timeouts.submit_wait_s)
            finally:
                self._restore_clipboard(saved, ours)
        finally:
            if was_minimized:
                with contextlib.suppress(Exception):
                    backend.set_minimized(window)
            if previous:
                with contextlib.suppress(Exception):
                    backend.restore_foreground(previous)
            log.info("desktop: flash to %r took %.2f s", title, self._monotonic() - started)

    def _press_role(
        self,
        profile: DesktopProfile,
        window: Control,
        title: str,
        role: Literal["clear_draft", "paste", "submit"],
        check: Callable[[], None] | None = None,
    ) -> None:
        """One press per key, the guards (and `check`, when given) run right before each one."""
        for keys in profile.keys.sendkeys(role):
            self._guard(profile, window, title)
            if check is not None:
                check()
            self._backend.press(keys)

    def _check_clipboard(self, ours: int, when: str) -> None:
        if self._clipboard.sequence() != ours:
            raise FocusLost(f"another program changed the clipboard {when}")

    def _restore_clipboard(self, saved: object, ours: int | None) -> None:
        """Put the old content back, but only while the clipboard still holds our text: a copy
        made during the flash (by the person or another program) is theirs and stays."""
        try:
            if ours is not None and self._clipboard.sequence() != ours:
                log.info("desktop: the clipboard changed during the flash, the previous content is not restored")
                return
            self._clipboard.restore(saved)
        except OSError:
            log.warning("desktop: could not restore the previous clipboard", exc_info=True)

    def _wait_pasted(self, profile: DesktopProfile, window: Control, text: str) -> None:
        paste = profile.paste
        if len(text) > paste.verify_max_chars:
            self._sleep(paste.unverified_wait_s)
            return
        head = _squash(text)[: paste.verify_head_chars]
        landed = self._wait(
            profile, lambda: head in _squash(self._backend.prompt_text(window)), profile.timeouts.paste_wait_s
        )
        if not landed:
            raise FocusLost("the pasted text did not appear in the prompt box")


def _resolve_profile(source: ProfileSource) -> DesktopProfile:
    if source is None:
        return load_packaged()
    return source if isinstance(source, DesktopProfile) else source()


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


# --- the real backend: uiautomation + user32 -------------------------------------------------

_SW_RESTORE: Final = 9
_SW_SHOW: Final = 5
_SW_SHOWMINNOACTIVE: Final = 7
_GA_ROOT: Final = 2
_DESKTOP_SWITCHDESKTOP: Final = 0x0100


@functools.cache
def _user32() -> Control:
    if sys.platform != "win32":
        raise DesktopUnavailable("the Claude Desktop port needs Windows")
    from ctypes import wintypes

    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.GetForegroundWindow.restype = ctypes.c_void_p
    dll.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    dll.GetAncestor.restype = ctypes.c_void_p
    dll.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    dll.BringWindowToTop.argtypes = [ctypes.c_void_p]
    dll.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    dll.IsIconic.argtypes = [ctypes.c_void_p]
    dll.IsWindow.argtypes = [ctypes.c_void_p]
    dll.IsWindowVisible.argtypes = [ctypes.c_void_p]
    dll.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    dll.GetWindowThreadProcessId.restype = wintypes.DWORD
    dll.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    dll.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenInputDesktop.restype = ctypes.c_void_p
    dll.CloseDesktop.argtypes = [ctypes.c_void_p]
    return dll


def _force_foreground(handle: int) -> None:
    """SetForegroundWindow, attached to the current foreground thread's input queue so Windows
    does not refuse it (no synthetic key presses, which could open a menu in the app in front)."""
    user32 = _user32()
    if user32.GetForegroundWindow() == handle:
        return
    from ctypes import wintypes

    current = ctypes.windll.kernel32.GetCurrentThreadId()  # type: ignore[attr-defined,unused-ignore]
    front = user32.GetForegroundWindow()
    front_thread = user32.GetWindowThreadProcessId(front, None) if front else 0
    attached = bool(front_thread and front_thread != current and user32.AttachThreadInput(current, front_thread, True))
    try:
        user32.BringWindowToTop(handle)
        user32.SetForegroundWindow(handle)
    finally:
        if attached:
            user32.AttachThreadInput(wintypes.DWORD(current), wintypes.DWORD(front_thread), False)


def _walk(control: Control, max_depth: int = 90) -> Iterator[tuple[Control, int]]:
    """Depth-first over the UI Automation tree (a generator, so a search can stop early)."""
    stack = [(control, 0)]
    while stack:
        node, depth = stack.pop()
        yield node, depth
        if depth < max_depth:
            stack.extend((child, depth + 1) for child in reversed(node.GetChildren()))


def _find_step(root: Control, step: Step) -> Control | None:
    for control, _ in _walk(root, step.max_depth):
        if step.match.matches(control):
            return control
    return None


def _find(root: Control, locator: Locator) -> Control | None:
    """Follow the locator's steps, each searched inside the previous result."""
    node: Control | None = root
    for step in locator.steps:
        node = _find_step(node, step)
        if node is None:
            return None
    return node


def _step_names(root: Control, step: Step) -> list[str]:
    """Names of the controls matching `step` (the root included), in tree order."""
    return [str(c.Name) for c, _ in _walk(root, step.max_depth) if step.match.matches(c)]


# --- the process behind a window: the hard guard that no profile can change --------------------

_PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000


@functools.cache
def _kernel32() -> Control:
    from ctypes import wintypes

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenProcess.restype = ctypes.c_void_p
    dll.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    dll.CloseHandle.argtypes = [ctypes.c_void_p]
    return dll


def process_image_of(handle: int) -> str:
    """Full path of the executable that owns the window `handle` ("" when it cannot be read)."""
    if sys.platform != "win32" or not handle:
        return ""
    from ctypes import wintypes

    pid = wintypes.DWORD()
    _user32().GetWindowThreadProcessId(handle, ctypes.byref(pid))
    if not pid.value:
        return ""
    kernel32 = _kernel32()
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)) else ""
    finally:
        kernel32.CloseHandle(process)


def is_claude_image(path: str) -> bool:
    """True for the Claude Desktop executable: `claude.exe`, or any program of the Claude MSIX package
    (`...\\WindowsApps\\Claude_<version>_<arch>__<publisher>\\...`)."""
    if not path:
        return False
    image = PureWindowsPath(path)
    if image.name.lower() == "claude.exe":
        return True
    parts = [part.lower() for part in image.parts]
    return image.suffix.lower() == ".exe" and "windowsapps" in parts and any(p.startswith("claude_") for p in parts)


class UiaBackend:
    """`DesktopBackend` on the `uiautomation` package (loaded on first use), driven by a profile.

    `profile` is a `DesktopProfile`, a function returning the current one, or None for the packaged
    one. `session()` takes a snapshot, so one operation uses one profile throughout. Whatever the
    profile says, the window must belong to the Claude Desktop process and only the keys of the
    allowlist are sent.
    """

    def __init__(self, profile: ProfileSource = None) -> None:
        self._auto: Control = None
        self._source = profile
        self._tls = threading.local()

    def _a(self) -> Control:
        if self._auto is None:
            if sys.platform != "win32":
                raise DesktopUnavailable("the Claude Desktop port needs Windows")
            try:
                self._auto = importlib.import_module("uiautomation")
            except ImportError as exc:
                raise DesktopUnavailable(f"the uiautomation package is not installed ({exc})") from exc
        return self._auto

    def _p(self) -> DesktopProfile:
        snapshot = getattr(self._tls, "profile", None)
        return snapshot if snapshot is not None else _resolve_profile(self._source)

    @contextlib.contextmanager
    def session(self) -> Iterator[None]:
        self._tls.profile = _resolve_profile(self._source)
        try:
            with self._a().UIAutomationInitializerInThread():
                yield
        finally:
            self._tls.profile = None

    def is_locked(self) -> bool:
        desktop = _user32().OpenInputDesktop(0, False, _DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return True  # the secure desktop (lock screen, UAC) is in front
        _user32().CloseDesktop(desktop)
        return False

    def find_window(self) -> Control | None:
        profile = self._p()
        for window in self._a().GetRootControl().GetChildren():
            if window.ControlTypeName != "WindowControl":
                continue
            if not is_claude_image(process_image_of(window.NativeWindowHandle)):
                continue  # the profile's matcher only ever sees the Claude Desktop's own windows
            if profile.window.matches(window):
                return window
        return None

    def is_minimized(self, window: Control) -> bool:
        return bool(_user32().IsIconic(window.NativeWindowHandle))

    def set_minimized(self, window: Control) -> None:
        _user32().ShowWindow(window.NativeWindowHandle, _SW_SHOWMINNOACTIVE)

    def activate(self, window: Control) -> None:
        user32, handle = _user32(), window.NativeWindowHandle
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, _SW_RESTORE)
        elif not user32.IsWindowVisible(handle):
            user32.ShowWindow(handle, _SW_SHOW)
        _force_foreground(handle)

    def is_foreground(self, window: Control) -> bool:
        """The foreground top window is this window and belongs to the Claude Desktop process: the
        system focus, and so every key, is inside it."""
        user32 = _user32()
        want = user32.GetAncestor(window.NativeWindowHandle, _GA_ROOT)
        if not want or user32.GetAncestor(user32.GetForegroundWindow(), _GA_ROOT) != want:
            return False
        return is_claude_image(process_image_of(want))

    def foreground(self) -> int:
        return int(_user32().GetForegroundWindow() or 0)

    def restore_foreground(self, handle: int) -> None:
        user32 = _user32()
        if not user32.IsWindow(handle) or user32.IsIconic(handle):
            return
        _force_foreground(user32.GetAncestor(handle, _GA_ROOT) or handle)

    def sidebar_entries(self, window: Control) -> list[SidebarEntry]:
        sidebar_profile = self._p().sidebar
        sidebar = _find(window, sidebar_profile.container)
        if sidebar is None:
            return []
        entries: list[SidebarEntry] = []
        for control, _ in _walk(sidebar, sidebar_profile.row.max_depth):
            if not sidebar_profile.row.match.matches(control):
                continue
            title, status = self._row_texts(control)
            if title:
                entries.append(SidebarEntry(title, status, control))
        return entries

    def _row_texts(self, row: Control) -> tuple[str, str]:
        """(title, status) of a conversation row: the title is the first text of its label group,
        the status the text of its screen-reader status bar. Navigation items have no label group."""
        sidebar = self._p().sidebar
        title, status, has_label = "", "", False
        for child in row.GetChildren():
            if sidebar.label.matches(child):
                has_label = True
                texts = [name for name in _step_names(child, sidebar.label_text) if name]
                title = texts[0] if texts else ""
            elif sidebar.status.matches(child):
                status = " ".join(name for name in _step_names(child, sidebar.status_text) if name)
        return (title, status) if has_label else ("", "")

    def open_entry(self, entry: SidebarEntry) -> None:
        entry.control.GetInvokePattern().Invoke()

    def _conversation_document(self, window: Control) -> Control | None:
        """The open conversation: the document the profile describes (other web areas are unnamed)."""
        return _find(window, self._p().conversation.document)

    def document_name(self, window: Control) -> str:
        document = self._conversation_document(window)
        return str(document.Name) if document is not None else ""

    def _find_prompt(self, window: Control) -> Control | None:
        return _find(window, self._p().prompt.editor)

    def focus_prompt(self, window: Control) -> bool:
        prompt = self._find_prompt(window)
        return prompt is not None and bool(prompt.SetFocus())

    def focus_in_prompt(self) -> bool:
        focused = self._a().GetFocusedControl()
        return focused is not None and self._p().prompt.focus.matches(focused)

    def prompt_text(self, window: Control) -> str:
        """ProseMirror exposes no Value: the text lives in the Text children of the editor."""
        prompt = self._find_prompt(window)
        if prompt is None:
            return ""
        return "".join(_step_names(prompt, self._p().prompt.text)).strip()

    def press(self, keys: str) -> None:
        """Send one key. Only the allowlist can pass, whoever calls and whatever the profile says."""
        if keys not in ALLOWED_SENDKEYS:
            raise DesktopError(f"refusing to send keys outside the allowlist: {keys!r}")
        timing = self._p().keys
        self._a().SendKeys(keys, interval=timing.interval_s, waitTime=timing.wait_s)

    def visible_text(self, window: Control) -> list[str]:
        document = self._conversation_document(window)
        if document is None:
            return []
        lines = [name.strip() for name in _step_names(document, self._p().conversation.visible_text)]
        return [line for line in lines if line]


# --- the real clipboard ----------------------------------------------------------------------

_CF_UNICODETEXT: Final = 13
_GMEM_MOVEABLE: Final = 0x0002
_HWND_MESSAGE: Final = -3
# Formats whose handle is not global memory (bitmaps, palettes, metafiles, owner-drawn) or whose
# meaning is private to the owner: they cannot be copied as bytes, so they are not restored.
_UNCOPYABLE_FORMATS: Final = frozenset({2, 3, 9, 14, 0x80})
_UNCOPYABLE_RANGES: Final = ((0x0200, 0x03FF),)
_HISTORY_OPT_OUT: Final = (
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
)


@dataclass(frozen=True)
class ClipboardSnapshot:
    formats: tuple[tuple[int, bytes], ...]
    dropped: int  # formats that could not be saved (bitmap handles, too big, unreadable)


@functools.cache
def _clipboard_api() -> SimpleNamespace:
    if sys.platform != "win32":
        raise DesktopUnavailable("the clipboard needs Windows")
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.DestroyWindow.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    return SimpleNamespace(user32=user32, kernel32=kernel32)


class Win32Clipboard:
    """Save, replace and restore the clipboard (OpenClipboard needs an owner window to set data)."""

    def __init__(self, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    @contextlib.contextmanager
    def _open(self) -> Iterator[None]:
        api = _clipboard_api()
        owner = api.user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, _HWND_MESSAGE, None, None, None)
        try:
            for _ in range(40):  # another process may hold it open for a moment
                if api.user32.OpenClipboard(owner):
                    break
                self._sleep(0.025)
            else:
                raise OSError("the clipboard is held open by another process")
            try:
                yield
            finally:
                api.user32.CloseClipboard()
        finally:
            if owner:
                api.user32.DestroyWindow(owner)

    def snapshot(self) -> ClipboardSnapshot:
        api = _clipboard_api()
        saved: list[tuple[int, bytes]] = []
        dropped = total = 0
        with self._open():
            fmt = api.user32.EnumClipboardFormats(0)
            while fmt:
                data = None
                skip = fmt in _UNCOPYABLE_FORMATS or any(lo <= fmt <= hi for lo, hi in _UNCOPYABLE_RANGES)
                if not skip:
                    data = self._read(api, fmt)
                if data is None or total + len(data) > MAX_CLIPBOARD_BYTES:
                    dropped += 1
                else:
                    total += len(data)
                    saved.append((fmt, data))
                fmt = api.user32.EnumClipboardFormats(fmt)
        if dropped:
            log.info("desktop: %d clipboard format(s) cannot be saved and will not be restored", dropped)
        return ClipboardSnapshot(tuple(saved), dropped)

    @staticmethod
    def _read(api: SimpleNamespace, fmt: int) -> bytes | None:
        handle = api.user32.GetClipboardData(fmt)
        if not handle:
            return None
        size = api.kernel32.GlobalSize(handle)
        if not size or size > MAX_CLIPBOARD_BYTES:
            return None
        pointer = api.kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.string_at(pointer, size)
        finally:
            api.kernel32.GlobalUnlock(handle)

    @staticmethod
    def _put(api: SimpleNamespace, fmt: int, data: bytes) -> bool:
        handle = api.kernel32.GlobalAlloc(_GMEM_MOVEABLE, max(1, len(data)))
        if not handle:
            return False
        pointer = api.kernel32.GlobalLock(handle)
        if not pointer:
            api.kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(pointer, data, len(data))
        api.kernel32.GlobalUnlock(handle)
        if not api.user32.SetClipboardData(fmt, handle):
            api.kernel32.GlobalFree(handle)  # the system owns it only after a successful set
            return False
        return True

    def set_text(self, text: str) -> None:
        api = _clipboard_api()
        data = (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\0").encode("utf-16-le")
        with self._open():
            api.user32.EmptyClipboard()
            if not self._put(api, _CF_UNICODETEXT, data):
                raise OSError("could not put the text on the clipboard")
            # Keep it out of Win+V history and the cloud clipboard (best effort).
            for name in _HISTORY_OPT_OUT:
                fmt = api.user32.RegisterClipboardFormatW(name)
                if fmt:
                    self._put(api, fmt, (0).to_bytes(4, "little"))

    def sequence(self) -> int:
        return int(_clipboard_api().user32.GetClipboardSequenceNumber())

    def restore(self, snapshot: object) -> None:
        if not isinstance(snapshot, ClipboardSnapshot):
            return
        api = _clipboard_api()
        with self._open():
            api.user32.EmptyClipboard()
            for fmt, data in snapshot.formats:
                self._put(api, fmt, data)
