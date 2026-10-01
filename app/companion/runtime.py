"""Threads for the controller: one dispatcher (state owner) and serial worker queues."""

from __future__ import annotations

import heapq
import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Final

log = logging.getLogger(__name__)

Task = Callable[[], None]
_STOP: Final = object()


class TimerHandle:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Dispatcher:
    """Runs posted tasks and timers on ONE thread, in order. Exceptions are logged, never fatal."""

    def __init__(self, name: str = "companion-dispatcher") -> None:
        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._timers: list[tuple[float, int, TimerHandle, Task]] = []
        self._timer_lock = threading.Lock()
        self._counter = itertools.count()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def on_thread(self) -> bool:
        return threading.current_thread() is self._thread

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    def post(self, task: Task) -> None:
        if not self._stopped.is_set():
            self._queue.put(task)

    def call_later(self, delay_s: float, task: Task) -> TimerHandle:
        handle = TimerHandle()
        with self._timer_lock:
            heapq.heappush(self._timers, (time.monotonic() + max(0.0, delay_s), next(self._counter), handle, task))
        self._queue.put(None)  # wake the loop so it recomputes its wait
        return handle

    def stop(self) -> None:
        self._stopped.set()
        self._queue.put(_STOP)

    def join(self, timeout: float) -> None:
        if not self.on_thread():
            self._thread.join(timeout)

    def _due(self) -> tuple[list[Task], float | None]:
        now = time.monotonic()
        due: list[Task] = []
        with self._timer_lock:
            while self._timers and self._timers[0][0] <= now:
                _when, _seq, handle, task = heapq.heappop(self._timers)
                if not handle.cancelled:
                    due.append(task)
            wait = self._timers[0][0] - now if self._timers else None
        return due, wait

    def _run_task(self, task: Task) -> None:
        try:
            task()
        except Exception:  # noqa: BLE001 - the dispatcher must survive any handler
            log.exception("dispatcher task failed")

    def _run(self) -> None:
        while True:
            due, wait = self._due()
            for task in due:
                self._run_task(task)
            try:
                item = self._queue.get(timeout=wait if wait is not None else 1.0)
            except queue.Empty:
                continue
            if item is _STOP:
                return
            if callable(item):
                self._run_task(item)


class SerialExecutor:
    """A worker thread running submitted tasks one at a time, in order."""

    def __init__(self, name: str) -> None:
        self._name = name
        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False

    def submit(self, task: Task) -> None:
        with self._lock:
            if self._stopped:
                return
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
                self._thread.start()
        self._queue.put(task)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
        self._queue.put(_STOP)

    def on_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            if callable(item):
                try:
                    item()
                except Exception:  # noqa: BLE001 - keep the worker alive
                    log.exception("%s task failed", self._name)
