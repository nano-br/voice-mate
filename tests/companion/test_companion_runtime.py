from __future__ import annotations

import functools
import threading
import time

from companion_core_fakes import wait_until

from app.companion.runtime import Dispatcher, SerialExecutor


def test_dispatcher_runs_tasks_in_order_on_one_thread() -> None:
    dispatcher = Dispatcher(name="test-dispatcher")
    seen: list[tuple[int, str]] = []

    def record(i: int) -> None:
        seen.append((i, threading.current_thread().name))

    for i in range(50):
        dispatcher.post(functools.partial(record, i))
    assert wait_until(lambda: len(seen) == 50)
    assert [i for i, _name in seen] == list(range(50))
    assert {name for _i, name in seen} == {"test-dispatcher"}
    dispatcher.stop()


def test_dispatcher_survives_failing_tasks() -> None:
    dispatcher = Dispatcher()
    seen: list[str] = []

    def boom() -> None:
        raise RuntimeError("boom")

    dispatcher.post(boom)
    dispatcher.post(lambda: seen.append("after"))
    assert wait_until(lambda: seen == ["after"])
    dispatcher.stop()


def test_timers_fire_in_time_order_and_can_be_cancelled() -> None:
    dispatcher = Dispatcher()
    fired: list[str] = []
    started = time.monotonic()
    dispatcher.call_later(0.3, lambda: fired.append("late"))
    dispatcher.call_later(0.1, lambda: fired.append("early"))
    cancelled = dispatcher.call_later(0.2, lambda: fired.append("cancelled"))
    cancelled.cancel()
    assert wait_until(lambda: len(fired) == 2)
    assert fired == ["early", "late"]
    assert time.monotonic() - started >= 0.29
    dispatcher.stop()
    dispatcher.post(lambda: fired.append("after stop"))
    time.sleep(0.1)
    assert fired == ["early", "late"]


def test_serial_executor_runs_one_at_a_time_in_order() -> None:
    executor = SerialExecutor("test-worker")
    seen: list[int] = []
    active = []
    overlap = []

    def task(i: int) -> None:
        active.append(i)
        if len(active) > 1:
            overlap.append(i)
        time.sleep(0.005)
        seen.append(i)
        active.remove(i)

    for i in range(20):
        executor.submit(functools.partial(task, i))
    assert wait_until(lambda: len(seen) == 20)
    assert seen == list(range(20)) and overlap == []

    def boom() -> None:
        raise RuntimeError("boom")

    executor.submit(boom)
    executor.submit(lambda: seen.append(99))
    assert wait_until(lambda: seen[-1] == 99)
    executor.stop()
    executor.submit(lambda: seen.append(100))
    time.sleep(0.05)
    assert seen[-1] == 99
