"""The engine on this machine (Linux `local` mode): `bash -lc 'cd ... && exec make run-engine'`.

The engine keeps its native hotkey listener and clipboard there (`--api` adds the HTTP
API next to it); the companion supervises it and plays the cues. The process gets its
own session so a kill reaches `make` and the Python child alike.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

from app.companion.client import DaemonClient
from app.companion.dictation import EngineLanguage
from app.companion.paths import home_dir
from app.companion.supervisor.backend import (
    ENGINE_DIR_CANDIDATES,
    EngineLog,
    ExitCallback,
    ManagedProcess,
    engine_script,
    env_token,
    read_local_token,
    stop_gracefully,
)
from app.protocol.models import ShutdownReason

log = logging.getLogger(__name__)


class LocalBackend:
    can_spawn = True
    can_restart_wsl = False

    def __init__(
        self, port: int, log_path: Path, *, language: EngineLanguage | None = None, shell: str = "bash"
    ) -> None:
        self.port = port
        self.language = language
        self._shell = shell
        self._log = EngineLog(log_path)
        self._lock = threading.Lock()
        self._process: ManagedProcess | None = None

    def spawn_command(self, engine_dir: str) -> list[str]:
        return [self._shell, "-lc", engine_script(engine_dir, self.port, self.language)]

    def spawn(self, engine_dir: str, generation: int, on_exit: ExitCallback) -> None:
        command = self.spawn_command(engine_dir)
        self._log.write(f"[companion] starting: {' '.join(command)}")
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=sys.platform != "win32",
        )

        def kill_tree() -> None:
            if sys.platform != "win32":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        managed = ManagedProcess(
            process, generation=generation, engine_log=self._log, on_exit=on_exit, kill_tree=kill_tree
        )
        with self._lock:
            self._process = managed

    def owned(self) -> ManagedProcess | None:
        with self._lock:
            process = self._process
        return process if process is not None and process.running() else None

    def stop(self, client: DaemonClient | None, reason: ShutdownReason) -> None:
        process = self.owned()
        if process is not None:
            stop_gracefully(process, client, reason)

    def read_token(self) -> str | None:
        return env_token() or read_local_token()

    def systemd_unit_enabled(self) -> bool:
        systemctl = shutil.which("systemctl")
        if systemctl is None:
            return False
        try:
            result = subprocess.run(
                [systemctl, "--user", "is-enabled", "voicemate"], capture_output=True, text=True, timeout=5, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.stdout.strip() in ("enabled", "enabled-runtime")

    def detect_engine_dir(self) -> str | None:
        home = home_dir()
        if home is None:
            return None  # no home directory: the user sets the engine folder in Settings
        for candidate in ENGINE_DIR_CANDIDATES:
            root = home / candidate
            if (root / "Makefile").is_file() and (root / "app" / "main.py").is_file():
                return candidate
        return None

    def ensure_keepalive(self) -> None:
        return None

    def stop_keepalive(self) -> None:
        return None

    def shutdown_wsl(self) -> None:
        return None

    def other_running_distros(self) -> list[str]:
        return []

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is not None and process.running():
            process.kill()
        self._log.close()
