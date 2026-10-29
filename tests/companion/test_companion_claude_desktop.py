"""The Claude Desktop port against a fake backend and a fake clipboard: no real window is touched."""

from __future__ import annotations

import json
import sys
import threading
from contextlib import nullcontext
from types import ModuleType

import pytest

from app.companion.win import claude_desktop as cd
from app.companion.win.claude_desktop import (
    AmbiguousConversation,
    Control,
    ConversationMissing,
    DesktopBusy,
    DesktopError,
    DesktopSender,
    DesktopUnavailable,
    FocusLost,
    ScreenLocked,
    SidebarEntry,
    WindowMissing,
)
from app.companion.win.desktop_profile import PACKAGED_PROFILE_PATH, DesktopProfile, load_packaged, parse_profile

TITLE = "VoiceMate central"
WIN = object()  # the window handle the fake returns


class Clock:
    """Virtual time: sleeping advances it, so no test waits for real."""

    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


class FakeClipboard:
    def __init__(self, content: object = "previous") -> None:
        self.content: object = content
        self.events: list[str] = []
        self.seq = 1
        self.snapshot_error: Exception | None = None

    def snapshot(self) -> object:
        if self.snapshot_error is not None:
            raise self.snapshot_error
        self.events.append("snapshot")
        return ("snap", self.content)

    def set_text(self, text: str) -> None:
        self.events.append("set_text")
        self.content = text
        self.seq += 1

    def restore(self, snapshot: object) -> None:
        self.events.append("restore")
        assert isinstance(snapshot, tuple)
        self.content = snapshot[1]
        self.seq += 1

    def sequence(self) -> int:
        return self.seq

    def copy(self, content: object) -> None:
        """Another program (or the person) copies something."""
        self.content = content
        self.seq += 1


class FakeBackend:
    """A Claude Desktop with a sidebar, a prompt box and a foreground, driven by `press`."""

    def __init__(self, clipboard: FakeClipboard, titles: list[str] | None = None, open_title: str = TITLE) -> None:
        self.clipboard = clipboard
        self.entries = [SidebarEntry(t, "", t) for t in (titles if titles is not None else [TITLE, "Other"])]
        self.open_title = open_title
        self.locked = False
        self.window: object | None = WIN
        self.minimized = False
        self.foreground_handle = 111  # the app the person was using
        self.claude_handle = 999
        self.refuse_activate = False
        self.focus_ok = True
        self.paste_lands = True
        self.switch_on_key: tuple[str, str] | None = None  # (key press that switches, new title)
        self.prompt = ""
        self.sent: list[str] = []
        self.keys: list[str] = []
        self.log: list[str] = []
        self.hold: threading.Event | None = None  # blocks `press` (to test the lock)
        self.entered = threading.Event()
        self.fail_on: str | None = None
        self.fail_with: Exception = OSError("SendInput failed")
        self.steal_on_key: str | None = None  # a key after which another app takes the foreground
        self.copy_on_key: tuple[str, str] | None = None  # (key, what another program copies after it)
        self.minimize_error: Exception | None = None
        self.copy_after_set: str | None = None  # copied by another program right after our text went on

    def session(self) -> Control:
        return nullcontext()

    def is_locked(self) -> bool:
        return self.locked

    def find_window(self) -> Control | None:
        return self.window

    def is_minimized(self, window: Control) -> bool:
        return self.minimized

    def set_minimized(self, window: Control) -> None:
        if self.minimize_error is not None:
            raise self.minimize_error
        self.log.append("minimize")
        self.minimized = True

    def activate(self, window: Control) -> None:
        self.log.append("activate")
        if not self.refuse_activate:
            self.minimized = False
            self.foreground_handle = self.claude_handle

    def is_foreground(self, window: Control) -> bool:
        if self.copy_after_set is not None and "set_text" in self.clipboard.events:
            self.clipboard.copy(self.copy_after_set)
            self.copy_after_set = None
        return self.foreground_handle == self.claude_handle

    def foreground(self) -> int:
        return self.foreground_handle

    def restore_foreground(self, handle: int) -> None:
        self.log.append(f"restore_foreground {handle}")
        self.foreground_handle = handle

    def sidebar_entries(self, window: Control) -> list[SidebarEntry]:
        return list(self.entries)

    def open_entry(self, entry: SidebarEntry) -> None:
        self.log.append(f"open {entry.title}")
        if entry.control != "wrong":
            self.open_title = entry.title

    def document_name(self, window: Control) -> str:
        return f"{self.open_title} - Claude Code" if self.open_title else ""

    def focus_prompt(self, window: Control) -> bool:
        return True

    def focus_in_prompt(self) -> bool:
        return self.focus_ok

    def prompt_text(self, window: Control) -> str:
        return self.prompt

    def visible_text(self, window: Control) -> list[str]:
        return ["line 1", "line 2"]

    def press(self, keys: str) -> None:
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(5)
        if keys == self.fail_on:
            raise self.fail_with
        self.keys.append(keys)
        self.log.append(f"press {keys}")
        if self.switch_on_key and keys == self.switch_on_key[0]:
            self.open_title = self.switch_on_key[1]
        if keys == self.steal_on_key:
            self.foreground_handle = 555
        if self.copy_on_key and keys == self.copy_on_key[0]:
            self.clipboard.copy(self.copy_on_key[1])
        if keys == "{Delete}":
            self.prompt = ""
        elif keys == "{Ctrl}v" and self.paste_lands:
            self.prompt = str(self.clipboard.content)
        elif keys == "{Enter}":
            self.sent.append(self.prompt)
            self.prompt = ""


def make(
    titles: list[str] | None = None, open_title: str = TITLE
) -> tuple[DesktopSender, FakeBackend, FakeClipboard, Clock]:
    clock = Clock()
    clipboard = FakeClipboard()
    backend = FakeBackend(clipboard, titles, open_title)
    sender = DesktopSender(backend, clipboard, sleep=clock.sleep, monotonic=clock.monotonic, lock_wait_s=0.2)
    return sender, backend, clipboard, clock


# --- the flash ---------------------------------------------------------------------------------


def test_flash_types_the_text_and_gives_everything_back() -> None:
    sender, backend, clipboard, _ = make()
    sender.send(TITLE, "ola mundo")
    assert backend.sent == ["ola mundo"]
    assert backend.keys == ["{Ctrl}a", "{Delete}", "{Ctrl}v", "{Enter}"]
    assert clipboard.content == "previous"  # restored
    assert clipboard.events == ["snapshot", "set_text", "restore"]
    assert backend.foreground_handle == 111  # the person's app is back in front
    assert backend.log[0] == "activate"  # no key before the window is in front
    assert "minimize" not in backend.log


def test_a_minimized_window_is_restored_for_the_flash_and_minimized_again() -> None:
    sender, backend, _, _ = make()
    backend.minimized = True
    sender.send(TITLE, "oi")
    assert backend.sent == ["oi"]
    assert backend.minimized is True
    assert backend.log[-2:] == ["minimize", "restore_foreground 111"]


def test_another_open_conversation_is_switched_in_the_background_before_the_window_comes_forward() -> None:
    sender, backend, _, _ = make(open_title="Other")
    sender.send(TITLE, "oi")
    assert backend.log[:2] == [f"open {TITLE}", "activate"]
    assert backend.sent == ["oi"]


def test_multiline_text_and_surrounding_newlines() -> None:
    sender, backend, _, _ = make()
    sender.send(TITLE, "\nline one\nline two\n")
    assert backend.sent == ["line one\nline two"]


def test_long_text_is_not_verified_in_the_prompt_box() -> None:
    sender, backend, _, _ = make()
    backend.paste_lands = False  # a long paste may become an attachment: the prompt text stays empty
    sender.send(TITLE, "x" * (load_packaged().paste.verify_max_chars + 1))
    assert backend.keys[-1] == "{Enter}"


@pytest.mark.parametrize(("title", "text"), [("", "a"), ("  ", "a"), (TITLE, ""), (TITLE, " \n ")])
def test_empty_title_or_text_is_a_value_error(title: str, text: str) -> None:
    sender, backend, _, _ = make()
    with pytest.raises(ValueError):
        sender.send(title, text)
    assert backend.log == []


# --- refusals: nothing is typed ----------------------------------------------------------------


def _assert_untouched(backend: FakeBackend, clipboard: FakeClipboard) -> None:
    assert backend.keys == []
    assert backend.sent == []
    assert clipboard.events == []
    assert clipboard.content == "previous"


def test_screen_locked() -> None:
    sender, backend, clipboard, _ = make()
    backend.locked = True
    with pytest.raises(ScreenLocked) as info:
        sender.send(TITLE, "oi")
    assert info.value.code == "locked"
    _assert_untouched(backend, clipboard)
    assert backend.log == []


def test_window_missing() -> None:
    sender, backend, clipboard, _ = make()
    backend.window = None
    with pytest.raises(WindowMissing) as info:
        sender.send(TITLE, "oi")
    assert info.value.code == "window_missing"
    _assert_untouched(backend, clipboard)


def test_conversation_missing_does_not_even_activate_the_window() -> None:
    sender, backend, clipboard, _ = make(titles=["Other"], open_title="Other")
    with pytest.raises(ConversationMissing) as info:
        sender.send(TITLE, "oi")
    assert info.value.code == "conversation_missing"
    _assert_untouched(backend, clipboard)
    assert "activate" not in backend.log


def test_ambiguous_title() -> None:
    sender, backend, clipboard, _ = make(titles=[TITLE, TITLE, "Other"], open_title="Other")
    with pytest.raises(AmbiguousConversation) as info:
        sender.send(TITLE, "oi")
    assert info.value.code == "ambiguous"
    _assert_untouched(backend, clipboard)


def test_a_similar_title_is_not_the_title() -> None:
    sender, backend, clipboard, _ = make(titles=[TITLE + " 2", "Other"], open_title="Other")
    with pytest.raises(ConversationMissing):
        sender.send(TITLE, "oi")
    _assert_untouched(backend, clipboard)


def test_a_row_that_opens_another_conversation_is_refused() -> None:
    sender, backend, clipboard, _ = make(open_title="Other")
    backend.entries = [SidebarEntry(TITLE, "", "wrong"), SidebarEntry("Other", "", "Other")]
    with pytest.raises(ConversationMissing):
        sender.send(TITLE, "oi")
    _assert_untouched(backend, clipboard)
    assert "activate" not in backend.log


def test_the_foreground_is_refused_by_windows() -> None:
    sender, backend, clipboard, _ = make()
    backend.minimized = True
    backend.refuse_activate = True
    with pytest.raises(FocusLost) as info:
        sender.send(TITLE, "oi")
    assert info.value.code == "focus_lost"
    _assert_untouched(backend, clipboard)
    assert backend.log[-2:] == ["minimize", "restore_foreground 111"]


def test_focus_outside_the_prompt_box_types_nothing() -> None:
    sender, backend, clipboard, _ = make()
    backend.focus_ok = False
    with pytest.raises(FocusLost):
        sender.send(TITLE, "oi")
    assert backend.keys == []
    assert clipboard.events == ["snapshot", "restore"]  # saved and given back, never overwritten
    assert clipboard.content == "previous"
    assert backend.foreground_handle == 111


def test_the_conversation_changes_after_the_first_key() -> None:
    sender, backend, clipboard, _ = make()
    backend.switch_on_key = ("{Ctrl}a", "Other")
    with pytest.raises(ConversationMissing):
        sender.send(TITLE, "oi")
    assert backend.keys == ["{Ctrl}a"]  # not even the Delete of the same step
    assert clipboard.content == "previous"
    assert backend.sent == []


def test_a_paste_that_does_not_land_is_not_submitted() -> None:
    sender, backend, clipboard, _ = make()
    backend.paste_lands = False
    with pytest.raises(FocusLost):
        sender.send(TITLE, "short text")
    assert "{Enter}" not in backend.keys
    assert clipboard.content == "previous"
    assert backend.foreground_handle == 111


def test_the_clipboard_is_restored_when_a_key_fails() -> None:
    sender, backend, clipboard, _ = make()
    backend.fail_on = "{Ctrl}v"
    with pytest.raises(DesktopBusy, match="SendInput"):  # the OSError, typed
        sender.send(TITLE, "oi")
    assert clipboard.content == "previous"
    assert backend.foreground_handle == 111


def test_another_app_taking_the_foreground_after_the_first_key_stops_the_next_one() -> None:
    sender, backend, clipboard, _ = make()
    backend.steal_on_key = "{Ctrl}a"  # a click elsewhere between Ctrl+A and Delete
    with pytest.raises(FocusLost):
        sender.send(TITLE, "oi")
    assert backend.keys == ["{Ctrl}a"]  # the Delete never reaches the app in front
    assert backend.sent == []
    assert clipboard.content == "previous"


def test_a_clipboard_held_by_another_program_is_a_typed_error() -> None:
    sender, backend, clipboard, _ = make()
    clipboard.snapshot_error = OSError("the clipboard is held open by another process")
    with pytest.raises(DesktopBusy, match="held open") as info:
        sender.send(TITLE, "oi")
    assert info.value.code == "busy"
    assert backend.keys == []
    assert backend.foreground_handle == 111


def test_a_ui_automation_failure_is_a_typed_error() -> None:
    class COMError(Exception):  # what comtypes raises when the window reloads mid flash
        pass

    sender, backend, _, _ = make()
    backend.fail_on = "{Enter}"
    backend.fail_with = COMError("the element is not available")
    with pytest.raises(DesktopUnavailable, match="COMError") as info:
        sender.send(TITLE, "oi")
    assert info.value.code == "unavailable"
    backend.fail_on = None
    sender.send(TITLE, "again")  # the lock was released
    assert backend.sent == ["again"]


def test_a_copy_before_the_paste_stops_the_flash_and_is_kept() -> None:
    sender, backend, clipboard, _ = make()
    backend.copy_after_set = "mine"
    with pytest.raises(FocusLost, match="before the paste"):
        sender.send(TITLE, "oi")
    assert "{Ctrl}v" not in backend.keys
    assert clipboard.content == "mine"  # not overwritten by the restore
    assert "restore" not in clipboard.events


def test_a_copy_after_the_paste_stops_before_enter_and_is_kept() -> None:
    sender, backend, clipboard, _ = make()
    backend.copy_on_key = ("{Ctrl}v", "mine")
    with pytest.raises(FocusLost, match="before Enter"):
        sender.send(TITLE, "x" * 3000)  # long: the paste is not verified, Enter would send the clipboard
    assert "{Enter}" not in backend.keys
    assert backend.sent == []
    assert clipboard.content == "mine"


def test_the_foreground_comes_back_even_when_minimizing_again_fails() -> None:
    sender, backend, _, _ = make()
    backend.minimized = True
    backend.minimize_error = RuntimeError("the window closed")
    sender.send(TITLE, "oi")
    assert backend.foreground_handle == 111
    assert backend.log[-1] == "restore_foreground 111"


def test_every_error_is_a_desktop_error_with_a_code() -> None:
    errors = (WindowMissing, ConversationMissing, AmbiguousConversation, FocusLost, ScreenLocked, DesktopBusy)
    assert {cls.code for cls in errors} == {
        "window_missing",
        "conversation_missing",
        "ambiguous",
        "focus_lost",
        "locked",
        "busy",
    }
    assert all(issubclass(cls, DesktopError) for cls in (*errors, DesktopUnavailable))
    assert str(FocusLost("detail")) == "detail"
    assert FocusLost().detail == "focus_lost"


# --- one flash at a time -------------------------------------------------------------------------


def test_a_second_flash_waits_then_gives_up_as_busy() -> None:
    sender, backend, _, _ = make()
    backend.hold = threading.Event()
    first = threading.Thread(target=lambda: sender.send(TITLE, "first"))
    first.start()
    assert backend.entered.wait(5)  # the first flash is inside press(), holding the lock
    try:
        with pytest.raises(DesktopBusy) as info:  # the lock wait is a real 0.2 s timeout
            sender.send(TITLE, "second")
        assert info.value.code == "busy"
    finally:
        backend.hold.set()
        first.join(5)
    assert backend.sent == ["first"]
    sender.send(TITLE, "third")  # the lock was released
    assert backend.sent == ["first", "third"]


# --- reading -------------------------------------------------------------------------------------


def test_reading_does_not_activate_the_window() -> None:
    sender, backend, clipboard, _ = make(open_title="Other")
    assert sender.list_titles() == [TITLE, "Other"]
    assert sender.current_title() == "Other"
    assert sender.read_visible() == ["line 1", "line 2"]
    assert sender.read_visible(TITLE) == ["line 1", "line 2"]
    assert backend.open_title == TITLE
    sender.open_conversation("Other")
    assert backend.open_title == "Other"
    assert "activate" not in backend.log
    assert backend.keys == []
    assert clipboard.events == []


def test_reading_refuses_a_locked_screen_and_a_missing_window() -> None:
    sender, backend, _, _ = make()
    backend.locked = True
    with pytest.raises(ScreenLocked):
        sender.list_titles()
    backend.locked = False
    backend.window = None
    with pytest.raises(WindowMissing):
        sender.current_title()


# --- the sender follows the profile ------------------------------------------------------------


def _profile(version: int, **edits: object) -> DesktopProfile:
    data = json.loads(PACKAGED_PROFILE_PATH.read_text(encoding="utf-8"))
    data["profile_version"] = version
    for dotted, value in edits.items():
        *parents, leaf = dotted.split("__")
        node = data
        for key in parents:
            node = node[key]
        node[leaf] = value
    return parse_profile(json.dumps(data))


def test_the_sender_takes_the_current_profile_on_each_operation() -> None:
    sender, backend, _, _ = make()
    state = {"profile": _profile(2, conversation__title_format="{title} | Code")}
    sender._profile_source = lambda: state["profile"]
    # The fake still names the document "<title> - Claude Code", which this format does not describe.
    assert sender.current_title() == f"{TITLE} - Claude Code"
    state["profile"] = load_packaged()
    assert sender.current_title() == TITLE
    sender.send(TITLE, "oi")
    assert backend.keys == ["{Ctrl}a", "{Delete}", "{Ctrl}v", "{Enter}"]


def test_the_profile_changes_the_keys_the_waits_and_the_title() -> None:
    profile = _profile(
        2,
        keys__clear_draft=["escape", "ctrl+a", "delete"],
        paste__verify_max_chars=3,
        paste__unverified_wait_s=1.25,
    )
    clock = Clock()
    clipboard = FakeClipboard()
    backend = FakeBackend(clipboard)
    sender = DesktopSender(backend, clipboard, sleep=clock.sleep, monotonic=clock.monotonic, profile=profile)
    backend.paste_lands = False  # longer than 3 characters: not verified, waits 1.25 s instead
    sender.send(TITLE, "long enough")
    assert backend.keys == ["{Esc}", "{Ctrl}a", "{Delete}", "{Ctrl}v", "{Enter}"]
    assert clock.now >= 1.25
    assert sender._profile_source is profile


# --- the real modules, without touching the screen ----------------------------------------------


def test_the_uia_backend_reports_a_missing_package(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> ModuleType:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(cd.importlib, "import_module", missing)
    with pytest.raises(DesktopUnavailable) as info:
        cd.UiaBackend().find_window()
    assert info.value.code == "unavailable"


def test_building_the_default_sender_does_not_load_uiautomation(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(name: str) -> ModuleType:
        raise AssertionError(f"{name} must load lazily")

    monkeypatch.setattr(cd.importlib, "import_module", forbidden)
    assert hasattr(DesktopSender(), "send")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clipboard")
def test_the_real_clipboard_round_trip_restores_what_was_there() -> None:
    clipboard = cd.Win32Clipboard()
    original = clipboard.snapshot()
    assert isinstance(original, cd.ClipboardSnapshot)
    try:
        clipboard.set_text("voicemate flash test\nsecond line")
        during = clipboard.snapshot()
        assert isinstance(during, cd.ClipboardSnapshot)
        text = dict(during.formats)[13].decode("utf-16-le").rstrip("\0")
        assert text == "voicemate flash test\r\nsecond line"
        assert len([fmt for fmt, _ in during.formats if fmt >= 0xC000]) >= 3  # history and cloud opt-out
    finally:
        clipboard.restore(original)
    after = clipboard.snapshot()
    assert isinstance(after, cd.ClipboardSnapshot)
    assert after.formats == original.formats
