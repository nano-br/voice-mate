import os
import sys
import threading
import time


class Watchdog:
    """Monitor application health and restart if a hang is detected."""

    def __init__(self, timeout_seconds: int = 120) -> None:
        self._timeout = timeout_seconds
        self._last_heartbeat = time.monotonic()
        self._running = False
        self._lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._heartbeat_timer: threading.Timer | None = None

    def heartbeat(self) -> None:
        """Update the timestamp of the last heartbeat."""
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def start(self) -> None:
        """Start monitoring in the background."""
        self._running = True
        self._last_heartbeat = time.monotonic()

        # Monitoring thread
        self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread.start()

        # Periodic heartbeat (proves the app is alive even when idle)
        self._schedule_periodic_heartbeat()

        print(f"[Watchdog] Active. Timeout: {self._timeout}s")

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.cancel()

    def _schedule_periodic_heartbeat(self) -> None:
        """Schedule an automatic heartbeat every 30s."""
        if not self._running:
            return
        self.heartbeat()
        self._heartbeat_timer = threading.Timer(30.0, self._schedule_periodic_heartbeat)
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()

    def _monitor(self) -> None:
        """Monitoring loop — checks whether the heartbeat is up to date."""
        check_interval = self._timeout / 2
        while self._running:
            time.sleep(check_interval)
            with self._lock:
                elapsed = time.monotonic() - self._last_heartbeat
            if elapsed > self._timeout:
                print(f"[Watchdog] ⚠ No heartbeat for {elapsed:.0f}s. Restarting...", file=sys.stderr)
                self._restart()
                return

    @staticmethod
    def _restart() -> None:
        """Restart the current process."""
        os.execv(sys.executable, [sys.executable] + sys.argv)
