import os
import sys
import threading
import time


class Watchdog:
    """Monitora a saúde da aplicação e reinicia se detectar travamento."""

    def __init__(self, timeout_seconds: int = 120) -> None:
        self._timeout = timeout_seconds
        self._last_heartbeat = time.monotonic()
        self._running = False
        self._lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._heartbeat_timer: threading.Timer | None = None

    def heartbeat(self) -> None:
        """Atualiza o timestamp do último heartbeat."""
        with self._lock:
            self._last_heartbeat = time.monotonic()

    def start(self) -> None:
        """Inicia o monitoramento em background."""
        self._running = True
        self._last_heartbeat = time.monotonic()

        # Thread de monitoramento
        self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread.start()

        # Heartbeat periódico (prova que a app está viva mesmo ociosa)
        self._schedule_periodic_heartbeat()

        print(f"[Watchdog] Ativo. Timeout: {self._timeout}s")

    def stop(self) -> None:
        """Para o monitoramento."""
        self._running = False
        if self._heartbeat_timer is not None:
            self._heartbeat_timer.cancel()

    def _schedule_periodic_heartbeat(self) -> None:
        """Agenda heartbeat automático a cada 30s."""
        if not self._running:
            return
        self.heartbeat()
        self._heartbeat_timer = threading.Timer(30.0, self._schedule_periodic_heartbeat)
        self._heartbeat_timer.daemon = True
        self._heartbeat_timer.start()

    def _monitor(self) -> None:
        """Loop de monitoramento — verifica se heartbeat está atualizado."""
        check_interval = self._timeout / 2
        while self._running:
            time.sleep(check_interval)
            with self._lock:
                elapsed = time.monotonic() - self._last_heartbeat
            if elapsed > self._timeout:
                print(f"[Watchdog] ⚠ Sem heartbeat há {elapsed:.0f}s. Reiniciando...", file=sys.stderr)
                self._restart()
                return

    @staticmethod
    def _restart() -> None:
        """Reinicia o processo atual."""
        os.execv(sys.executable, [sys.executable] + sys.argv)
