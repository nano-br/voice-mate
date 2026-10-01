from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")

from app.companion.contract import CompanionController, CompanionSnapshot, TrayState  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402


def test_fake_controller_satisfies_the_protocol() -> None:
    controller: CompanionController = FakeController()  # checked by mypy
    assert controller.snapshot().tray_state == "stopped"


def test_subscribe_delivers_the_current_snapshot_at_once_and_unsubscribes() -> None:
    fake = FakeController()
    seen: list[int] = []
    unsubscribe = fake.subscribe(lambda snap: seen.append(snap.rev))
    fake.publish(tray_state="idle")
    unsubscribe()
    fake.publish(tray_state="recording")
    assert seen == [0, 1]


def test_toggle_records_and_moves_through_recording() -> None:
    fake = FakeController()
    fake.start()
    fake.toggle("clipboard")
    assert fake.snapshot().tray_state == "recording"
    fake.toggle("claude_chat")
    assert fake.snapshot().tray_state == "transcribing"
    assert fake.snapshot().flow == "claude_chat"
    assert fake.called("toggle") == [("clipboard",), ("claude_chat",)]


def test_check_hotkey_rules() -> None:
    fake = FakeController()
    assert fake.check_hotkey("ctrl+alt+v", "clipboard") == "ok"
    assert fake.check_hotkey("ctrl+alt+a", "clipboard") == "duplicate"
    assert fake.check_hotkey("v", "clipboard") == "invalid"
    assert fake.check_hotkey("ctrl+f12", "clipboard") == "invalid"
    assert fake.check_hotkey("f8", "clipboard") == "ok"


def test_quit_calls_on_done_from_another_thread() -> None:
    fake = FakeController()
    done = threading.Event()
    threads: list[str] = []

    def on_done() -> None:
        threads.append(threading.current_thread().name)
        done.set()

    fake.quit(on_done)
    assert done.wait(2)
    assert threads == ["fake-controller-quit"]


def test_autoplay_cycles_through_every_tray_state() -> None:
    fake = FakeController(autoplay=True, step_scale=0.005)
    states: set[TrayState] = set()
    lock = threading.Lock()

    def listen(snapshot: CompanionSnapshot) -> None:
        with lock:
            states.add(snapshot.tray_state)

    fake.subscribe(listen)
    fake.start()
    deadline = time.monotonic() + 5
    expected: set[TrayState] = {
        "starting",
        "idle",
        "recording",
        "transcribing",
        "thinking",
        "speaking",
        "ready",
        "warning",
        "restarting",
        "error",
    }
    while time.monotonic() < deadline:
        with lock:
            if expected <= states:
                break
        time.sleep(0.01)
    fake.quit(lambda: None)
    assert expected <= states
