from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.companion.contract import CompanionSnapshot, Notification  # noqa: E402
from app.companion.ui.bridge import ControllerBridge  # noqa: E402
from app.companion.ui.demo_controller import FakeController  # noqa: E402


def test_snapshots_published_on_another_thread_arrive_on_the_gui_thread_in_order(
    qapp: QApplication, process_events: Callable[..., bool]
) -> None:
    fake = FakeController()
    bridge = ControllerBridge(fake)
    seen: list[tuple[int, bool]] = []
    bridge.snapshot_changed.connect(lambda snap: seen.append((snap.rev, QThread.currentThread() is qapp.thread())))
    bridge.attach()

    def publisher() -> None:
        for _ in range(20):
            fake.publish(tray_state="recording")

    thread = threading.Thread(target=publisher)
    thread.start()
    thread.join()
    assert process_events(lambda: len(seen) == 21)
    assert [rev for rev, _gui in seen] == list(range(21))
    assert all(gui for _rev, gui in seen)
    assert bridge.snapshot.rev == 20
    bridge.detach()
    fake.publish()
    process_events()
    assert len(seen) == 21  # detached: nothing more


def test_an_older_snapshot_never_replaces_a_newer_one(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    fake = FakeController()
    bridge = ControllerBridge(fake)
    seen: list[int] = []
    bridge.snapshot_changed.connect(lambda snap: seen.append(snap.rev))
    bridge._snapshot_posted.emit(CompanionSnapshot(rev=5))
    bridge._snapshot_posted.emit(CompanionSnapshot(rev=3))
    process_events()
    assert seen == [5]
    assert bridge.snapshot.rev == 5


def test_notifications_and_calls_hop_to_the_gui_thread(qapp: QApplication, process_events: Callable[..., bool]) -> None:
    fake = FakeController()
    bridge = ControllerBridge(fake)
    received: list[tuple[str, bool]] = []
    bridge.notification_received.connect(
        lambda note: received.append((note.title, QThread.currentThread() is qapp.thread()))
    )
    bridge.attach()
    threading.Thread(target=lambda: fake.notify(Notification("info", "hello", "world"))).start()
    threading.Thread(
        target=lambda: bridge.call_soon(lambda: received.append(("call", QThread.currentThread() is qapp.thread())))
    ).start()
    assert process_events(lambda: len(received) == 2)
    assert sorted(received) == [("call", True), ("hello", True)]
    bridge.detach()


def test_run_async_runs_off_the_gui_thread_and_answers_on_it(
    qapp: QApplication, process_events: Callable[..., bool]
) -> None:
    bridge = ControllerBridge(FakeController())
    worker_threads: list[bool] = []
    results: list[tuple[int, bool]] = []
    errors: list[str] = []

    def work() -> int:
        worker_threads.append(QThread.currentThread() is qapp.thread())
        return 42

    def fail() -> int:
        raise RuntimeError("boom")

    bridge.run_async(work, lambda value: results.append((value, QThread.currentThread() is qapp.thread())))
    bridge.run_async(fail, lambda value: results.append((value, False)), lambda exc: errors.append(str(exc)))
    assert process_events(lambda: bool(results) and bool(errors))
    assert worker_threads == [False]
    assert results == [(42, True)]
    assert errors == ["boom"]
