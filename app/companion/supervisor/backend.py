"""What the supervisor needs from an engine host, plus the process plumbing all hosts share.

- `ManagedProcess`: an engine process with stdin held open (EOF = `--supervised`
  shutdown), stdout+stderr drained by a thread into the engine log (the thread never
  stops draining, or the engine would block on a full pipe), and an exit watcher.
- `stop_gracefully`: POST /shutdown, wait up to 8 s, close stdin, wait 2 s, kill.
- `EngineBackend`: spawn/attach details per mode (`wsl.WslBackend`, `local.LocalBackend`,
  `ExternalBackend` here).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final, Protocol

from app.companion.client import DaemonClient, DaemonError
from app.companion.paths import home_dir
from app.protocol.models import ShutdownReason

log = logging.getLogger(__name__)

SHUTDOWN_WAIT_S: Final = 8.0
EOF_WAIT_S: Final = 2.0
KILL_WAIT_S: Final = 2.0
TOKEN_RELATIVE_PATH: Final = ".config/voicemate/api-token"
# Where `engine_dir` is looked for when the setting is empty (relative to $HOME).
ENGINE_DIR_CANDIDATES: Final = (
    "voice-mate",
    "ai-lab/voice-mate",
    "workspace/voice-mate",
    "projects/voice-mate",
    "src/voice-mate",
    "code/voice-mate",
    "dev/voice-mate",
    "git/voice-mate",
    "repos/voice-mate",
)
_ADDRESS_IN_USE_MARKERS: Final = ("address already in use", "address in use", "errno 98")
LOG_MAX_BYTES: Final = 5 * 1024 * 1024
LOG_BACKUPS: Final = 3

# Called with (generation, exit code, the output said "address already in use").
ExitCallback = Callable[[int, int, bool], None]


def engine_script(engine_dir: str, port: int) -> str:
    """`cd` into the engine checkout and run it. Never `shlex.quote` a `~` path: relative
    paths are joined to "$HOME" inside double quotes (settings reject `"`, `$`, backtick and
    newlines in `engine_dir`)."""
    directory = engine_dir.strip()
    target = f'"{directory}"' if directory.startswith("/") else f'"$HOME/{directory}"'
    return f'cd {target} && exec make run-engine ARGS="--daemon-port {port}"'


def env_token() -> str | None:
    """VOICEMATE_API_TOKEN overrides the token file (tests only, as on the daemon side)."""
    value = os.environ.get("VOICEMATE_API_TOKEN", "").strip()
    return value or None


class EngineLog:
    """engine.log through a RotatingFileHandler; logging errors never reach the caller."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._logger = logging.getLogger(f"voicemate.engine.{id(self):x}")
        self._logger.propagate = False
        self._logger.setLevel(logging.INFO)
        self._handler: RotatingFileHandler | None = None
        self._opened = False  # created on the first line: no file (or folder) for an unused backend
        self._lock = threading.Lock()

    def _open(self) -> None:
        self._opened = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                self.path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8", delay=True
            )
        except OSError as exc:
            log.warning("engine log %s unavailable: %s", self.path, exc)
            return
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self._logger.addHandler(handler)
        self._handler = handler

    def write(self, line: str) -> None:
        try:
            with self._lock:
                if not self._opened:
                    self._open()
            self._logger.info("%s", line)
        except Exception:  # noqa: BLE001 - draining matters more than logging
            pass

    def close(self) -> None:
        if self._handler is not None:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None


class ManagedProcess:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        generation: int,
        engine_log: EngineLog,
        on_exit: ExitCallback | None,
        kill_tree: Callable[[], None] | None = None,
        name: str = "engine",
    ) -> None:
        self.process = process
        self.generation = generation
        self._log = engine_log
        self._on_exit = on_exit
        self._kill_tree = kill_tree
        self._tail: deque[str] = deque(maxlen=50)
        self._pump = threading.Thread(target=self._drain, name=f"companion-{name}-log", daemon=True)
        self._pump.start()
        self._waiter = threading.Thread(target=self._wait, name=f"companion-{name}-exit", daemon=True)
        self._waiter.start()

    @property
    def pid(self) -> int:
        return self.process.pid

    def running(self) -> bool:
        return self.process.poll() is None

    def address_in_use(self) -> bool:
        return any(marker in line.lower() for line in list(self._tail) for marker in _ADDRESS_IN_USE_MARKERS)

    def tail(self) -> list[str]:
        return list(self._tail)

    def _drain(self) -> None:
        stream = self.process.stdout
        if stream is None:
            return
        while True:
            try:
                raw = stream.readline()
            except (OSError, ValueError):
                break
            if not raw:
                break
            try:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._tail.append(line)
                self._log.write(line)
            except Exception:  # noqa: BLE001 - never stop draining
                continue

    def _wait(self) -> None:
        code = self.process.wait()
        self._pump.join(2.0)
        self._log.write(f"[companion] engine process exited with code {code}")
        if self._on_exit is not None:
            try:
                self._on_exit(self.generation, code, self.address_in_use())
            except Exception:  # noqa: BLE001
                log.exception("exit callback failed")

    def wait(self, timeout: float) -> bool:
        try:
            self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    def close_stdin(self) -> None:
        stdin = self.process.stdin
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass

    def kill(self) -> None:
        if self._kill_tree is not None:
            try:
                self._kill_tree()
            except OSError:
                log.exception("killing the process tree failed")
        if self.running():
            try:
                self.process.kill()
            except OSError:
                pass


def stop_gracefully(process: ManagedProcess | None, client: DaemonClient | None, reason: ShutdownReason) -> None:
    """POST /shutdown, wait up to 8 s, close stdin (EOF), wait 2 s, then kill."""
    asked = False
    if client is not None:
        try:
            client.shutdown(reason)
            asked = True
        except DaemonError as exc:
            log.info("shutdown request failed: %s", exc)
    if process is None:
        return
    if asked and process.wait(SHUTDOWN_WAIT_S):
        return
    process.close_stdin()
    if process.wait(SHUTDOWN_WAIT_S if not asked else EOF_WAIT_S):
        return
    log.warning("engine pid %s did not stop; killing it", process.pid)
    process.kill()
    process.wait(KILL_WAIT_S)


def hidden_window_flags() -> int:
    return 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


class EngineBackend(Protocol):
    can_spawn: bool
    can_restart_wsl: bool

    def spawn(self, engine_dir: str, generation: int, on_exit: ExitCallback) -> None: ...

    def owned(self) -> ManagedProcess | None: ...

    def stop(self, client: DaemonClient | None, reason: ShutdownReason) -> None:
        """Gracefully stop the engine this app started (no-op when none runs)."""

    def read_token(self) -> str | None: ...

    def systemd_unit_enabled(self) -> bool: ...

    def detect_engine_dir(self) -> str | None: ...

    def ensure_keepalive(self) -> None: ...

    def stop_keepalive(self) -> None: ...

    def shutdown_wsl(self) -> None: ...

    def other_running_distros(self) -> list[str]: ...

    def close(self) -> None: ...


def read_local_token() -> str | None:
    home = home_dir()
    if home is None:
        return None
    path = home / TOKEN_RELATIVE_PATH
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


class ExternalBackend:
    """`external` mode: never spawn, only attach to whatever answers on the port."""

    can_spawn = False
    can_restart_wsl = False

    def __init__(self, token_reader: Callable[[], str | None] | None = None) -> None:
        self._token_reader = token_reader or read_local_token

    def spawn(self, engine_dir: str, generation: int, on_exit: ExitCallback) -> None:
        raise OSError("external mode never spawns the engine")

    def owned(self) -> ManagedProcess | None:
        return None

    def stop(self, client: DaemonClient | None, reason: ShutdownReason) -> None:
        return None

    def read_token(self) -> str | None:
        return env_token() or self._token_reader()

    def systemd_unit_enabled(self) -> bool:
        return False

    def detect_engine_dir(self) -> str | None:
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
        return None
