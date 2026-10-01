"""Windows modules against the real OS: hotkeys, clipboard, Core Audio, job objects, sound, registry.

Uses unused chords (Ctrl+Alt+Shift+F2x), restores the user's clipboard text, plays one
quiet sound, and writes the registry only under a throwaway test key.
"""

from __future__ import annotations

import ctypes
import math
import subprocess
import sys
import threading
import time
import uuid
import wave
from array import array
from collections.abc import Callable, Iterator
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest

from app.companion.chords import KEY_NAMES, parse_chord

if sys.platform == "win32":
    import winreg

    from app.companion.win import audio_devices, autostart, hotkeys_clipboard, job, sound
    from app.companion.win.hotkeys_clipboard import HotkeyClipboardThread as Win32Thread
else:  # these modules only exist for type checkers on Windows
    winreg: Any = None
    audio_devices: Any = None
    autostart: Any = None
    hotkeys_clipboard: Any = None
    job: Any = None
    sound: Any = None
    Win32Thread = Any

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows APIs")

CHORD_A = "ctrl+alt+shift+f23"
CHORD_B = "ctrl+alt+shift+f22"


def _win_dll(name: str) -> Any:  # noqa: ANN401 - ctypes.WinDLL only exists (and type-checks) on Windows
    return getattr(ctypes, "WinDLL")(name, use_last_error=True)


def _process_alive(pid: int) -> bool:
    kernel32 = _win_dll("kernel32")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return bool(kernel32.WaitForSingleObject(handle, 0) == 0x102)  # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(0.05)
    return not _process_alive(pid)


@pytest.fixture
def pressed() -> list[str]:
    return []


@pytest.fixture
def win32_thread(pressed: list[str]) -> Iterator[Win32Thread]:
    thread = hotkeys_clipboard.HotkeyClipboardThread()
    thread.start(pressed.append)
    yield thread
    thread.stop()


def test_every_chord_key_has_a_virtual_key_code() -> None:
    assert set(KEY_NAMES) <= set(hotkeys_clipboard.VIRTUAL_KEYS)
    chord = parse_chord("ctrl+alt+shift+f23")
    assert chord is not None
    modifiers, vk = hotkeys_clipboard.hotkey_args(chord)
    assert vk == 0x86
    assert modifiers == 0x0002 | 0x0001 | 0x0004 | 0x4000  # ctrl, alt, shift, MOD_NOREPEAT


def test_hotkeys_register_fire_check_and_suspend(win32_thread: Win32Thread, pressed: list[str]) -> None:
    other = hotkeys_clipboard.HotkeyClipboardThread()
    other.start(lambda flow: None)
    try:
        assert win32_thread.register({"clipboard": CHORD_A}) == {"clipboard": "ok"}
        assert win32_thread.registered() == {"clipboard": CHORD_A}
        assert win32_thread.check(CHORD_A) == "ok"  # our own registration is not "in use"
        assert other.check(CHORD_A) == "in_use"  # taken for everybody else
        assert other.check("ctrl+f12") == "invalid"

        hotkey_id = win32_thread.hotkey_id("clipboard")
        assert hotkey_id is not None
        assert win32_thread.post_hotkey_message(hotkey_id)
        assert _until(lambda: pressed == ["clipboard"])

        # Atomic apply: one chord taken by someone else -> nothing changes.
        assert other.register({"claude_chat": CHORD_B}) == {"claude_chat": "ok"}
        verdicts = win32_thread.register({"clipboard": CHORD_A, "claude_chat": CHORD_B}, atomic=True)
        assert verdicts == {"clipboard": "ok", "claude_chat": "in_use"}
        assert win32_thread.registered() == {"clipboard": CHORD_A}
        assert other.check(CHORD_A) == "in_use"

        # Suspended while the settings window captures a chord: released, then back. suspend()
        # only posts; the next call on the same thread (registered) runs after it.
        win32_thread.suspend(True)
        assert win32_thread.registered() == {}
        assert win32_thread.check(CHORD_A) == "ok"  # still ours while suspended
        assert other.check(CHORD_A) == "ok"  # but free for everybody else
        win32_thread.suspend(False)
        assert win32_thread.registered() == {"clipboard": CHORD_A}
        assert other.check(CHORD_A) == "in_use"
    finally:
        other.stop()
    win32_thread.stop()
    second = hotkeys_clipboard.HotkeyClipboardThread()
    second.start(lambda flow: None)
    try:
        assert second.check(CHORD_A) == "ok"  # stop() unregistered everything
    finally:
        second.stop()


def _until(predicate: Callable[[], object], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _deliver(thread: Win32Thread, text: str, timeout: float = 5.0) -> bool | None:
    result: list[bool] = []
    done = threading.Event()

    def finished(ok: bool) -> None:
        result.append(ok)
        done.set()

    thread.deliver(text, finished)
    done.wait(timeout)
    return result[0] if result else None


@pytest.fixture
def saved_clipboard(win32_thread: Win32Thread) -> Iterator[None]:
    if win32_thread.has_non_text_content():
        pytest.skip("the clipboard holds non-text content we could not restore")
    original = win32_thread.read_text()
    yield
    if original:
        assert _deliver(win32_thread, original) is True


def test_clipboard_delivery_is_verified(win32_thread: Win32Thread, saved_clipboard: None) -> None:
    text = f"VoiceMate clipboard test {uuid.uuid4()}\nsecond line \u00e1\u00e7\u00e3\u00f5 \u2764"
    started = time.monotonic()
    assert _deliver(win32_thread, text) is True
    assert time.monotonic() - started >= 0.5  # read-back + Win+V re-assert + settle (no sleep on the thread)
    assert win32_thread.read_text() == text


def test_clipboard_waits_out_another_process_holding_it(win32_thread: Win32Thread, saved_clipboard: None) -> None:
    user32 = _win_dll("user32")
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    text = f"VoiceMate locked clipboard test {uuid.uuid4()}"
    result: list[bool] = []
    done = threading.Event()

    def finished(ok: bool) -> None:
        result.append(ok)
        done.set()

    # Held by this test thread (like another process would): the Win32 thread must skip its
    # attempts without blocking, then succeed once it is released.
    assert user32.OpenClipboard(None)
    try:
        win32_thread.deliver(text, finished)
        time.sleep(0.25)
        assert not done.is_set()
        assert win32_thread.read_text() is None  # the thread still answers calls meanwhile
    finally:
        user32.CloseClipboard()
    assert done.wait(5)
    assert result == [True]
    assert win32_thread.read_text() == text


def test_deliver_without_the_thread_fails_at_once() -> None:
    thread = hotkeys_clipboard.HotkeyClipboardThread()
    result: list[bool] = []
    thread.deliver("never", result.append)
    assert result == [False]


def test_same_text_tolerates_newline_differences() -> None:
    assert hotkeys_clipboard.same_text("a\r\nb\r\n", "a\nb")
    assert hotkeys_clipboard.same_text("a\n", "a")
    assert not hotkeys_clipboard.same_text("a", "b")
    assert not hotkeys_clipboard.same_text(None, "a")


def test_core_audio_counts_capture_devices() -> None:
    count = audio_devices.active_capture_count()
    assert count >= 0
    # Callable from any thread (COM is initialized per call).
    results: list[int] = []
    worker = threading.Thread(target=lambda: results.append(audio_devices.active_capture_count()))
    worker.start()
    worker.join(10)
    assert results == [count]


def test_job_object_kills_the_whole_tree() -> None:
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    process, job_object = job.popen_in_job(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, creationflags=job.CREATE_NO_WINDOW
    )
    try:
        assert process.stdout is not None
        grandchild = int(process.stdout.readline())  # it runs: the suspended start was resumed
        assert _process_alive(process.pid) and _process_alive(grandchild)
        job_object.terminate()
        assert _wait_dead(process.pid)
        assert _wait_dead(grandchild)  # a plain kill of the parent would leave this one running
    finally:
        job_object.close()
        if process.poll() is None:
            process.kill()


def test_closing_the_job_handle_kills_its_processes() -> None:
    process, job_object = job.popen_in_job(
        [sys.executable, "-c", "import time; time.sleep(60)"], creationflags=job.CREATE_NO_WINDOW
    )
    try:
        assert _process_alive(process.pid)
        job_object.close()  # what happens when the companion dies (KILL_ON_JOB_CLOSE)
        assert _wait_dead(process.pid)
    finally:
        if process.poll() is None:
            process.kill()


def test_winsound_plays_a_rendered_cue(tmp_path: Path) -> None:
    wav = tmp_path / "quiet.wav"
    # One short, quiet beep (660 Hz, 120 ms, 5% of full scale).
    samples = array("h", (int(1600 * math.sin(2 * math.pi * 660 * i / 44100)) for i in range(5292)))
    with wave.open(str(wav), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(44100)
        writer.writeframes(samples.tobytes())
    player = sound.WinSound()
    player.play(wav)
    time.sleep(0.3)
    player.stop()


def test_autostart_writes_the_run_value_under_a_test_key() -> None:
    key_path = rf"Software\VoiceMateCompanionTest-{uuid.uuid4().hex}"
    command = [r"C:\Program Files\VoiceMate\VoiceMate.exe", "--autostart"]
    try:
        assert not autostart.autostart_enabled(key_path=key_path)
        autostart.set_autostart(True, command, key_path=key_path)
        assert (
            autostart.autostart_command_line(key_path=key_path)
            == '"C:\\Program Files\\VoiceMate\\VoiceMate.exe" --autostart'
        )
        assert autostart.autostart_enabled(key_path=key_path)
        autostart.set_autostart(False, command, key_path=key_path)
        assert autostart.autostart_command_line(key_path=key_path) is None
        autostart.set_autostart(False, command, key_path=key_path)  # idempotent
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            pass
    assert autostart.VALUE_NAME == "VoiceMate"
    assert autostart.command_line([r"C:\venv\Scripts\pythonw.exe", "-m", "app.companion.main", "--autostart"]) == (
        '"C:\\venv\\Scripts\\pythonw.exe" -m app.companion.main --autostart'
    )


def test_create_desktop_wires_the_windows_services() -> None:
    from app.companion.desktop import create_desktop

    desktop = create_desktop()
    assert desktop.hotkeys is desktop.clipboard
    assert isinstance(desktop.sound, sound.WinSound)
    assert desktop.mic_count() >= 0
    assert desktop.autostart_enabled() in (True, False)
