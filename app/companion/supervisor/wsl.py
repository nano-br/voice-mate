"""The engine inside a WSL distro, driven through `wsl.exe` (docs/companion-app.md,
"Supervisor (WSL2)").

- spawn: `wsl.exe -d <distro> -e bash -lc 'cd "$HOME/<engine_dir>" && exec make run-engine
  ARGS="--daemon-port <port>"'`, CREATE_NO_WINDOW, stdin held open, output into engine.log,
  inside a Job Object (kill-on-close safety net);
- attach mode keepalive: `wsl.exe -d <distro> -e sleep infinity` (WSL stops an idle
  distro even with services running, and nothing else boots it after a WSL restart);
- `wsl.exe` helpers: the systemd unit check, the token file (`-e sh -c`, since `-e` does
  not expand `~`), `--shutdown`, and the running distros (`WSL_UTF8=1`).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from app.companion.client import DaemonClient
from app.companion.supervisor.backend import (
    ENGINE_DIR_CANDIDATES,
    TOKEN_RELATIVE_PATH,
    EngineLog,
    ExitCallback,
    ManagedProcess,
    engine_script,
    env_token,
    hidden_window_flags,
    stop_gracefully,
)
from app.protocol.models import ShutdownReason

log = logging.getLogger(__name__)

WSL_EXE: Final = "wsl.exe"
QUICK_TIMEOUT_S: Final = 20.0
SHUTDOWN_TIMEOUT_S: Final = 90.0
SYSTEMD_UNIT: Final = "voicemate"

Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[bytes]]


def run_hidden(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    """Run a short `wsl.exe` command without a console window, UTF-8 output."""
    return subprocess.run(
        list(args),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        env=dict(os.environ, WSL_UTF8="1"),
        creationflags=hidden_window_flags(),
        check=False,
    )


def _text(raw: bytes) -> str:
    # WSL_UTF8=1 gives UTF-8; older builds still print UTF-16LE for their own messages.
    if raw.count(b"\x00") > len(raw) // 4:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_distro_list(raw: bytes) -> list[str]:
    """`wsl.exe -l -q` output -> names."""
    names: list[str] = []
    for line in _text(raw).replace("﻿", "").splitlines():
        name = line.strip().strip("\x00")
        if name:
            names.append(name)
    return names


def parse_default_distro(raw: bytes) -> str:
    """`wsl.exe -l -v` output -> the distro marked with `*` ("" when none is)."""
    for line in _text(raw).replace("﻿", "").splitlines():
        stripped = line.strip()
        if stripped.startswith("*"):
            parts = stripped[1:].split()
            if parts:
                return parts[0]
    return ""


def read_wsl_token(distro: str, *, wsl_exe: str = WSL_EXE, runner: Runner = run_hidden) -> str | None:
    """The daemon's API token, read inside the distro (`-e` does not expand `~`)."""
    override = env_token()
    if override:
        return override
    base = [wsl_exe, "-d", distro] if distro else [wsl_exe]
    try:
        result = runner([*base, "-e", "sh", "-c", f'cat "$HOME/{TOKEN_RELATIVE_PATH}"'], QUICK_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("reading the API token through wsl.exe failed: %s", exc)
        return None
    if result.returncode != 0:
        return None
    token = result.stdout.decode("utf-8", errors="replace").strip()
    return token or None


def _spawn(
    args: list[str], *, stdout: int | None, stderr: int | None
) -> tuple[subprocess.Popen[bytes], Callable[[], None] | None, Callable[[], None] | None]:
    """Popen inside a Job Object on Windows -> (process, kill tree, release job)."""
    flags = hidden_window_flags()
    if sys.platform == "win32":
        from app.companion.win.job import popen_in_job

        process, job = popen_in_job(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, creationflags=flags)
        return process, job.terminate, job.close
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
    return process, None, None


class WslBackend:
    can_spawn = True
    can_restart_wsl = True

    def __init__(
        self,
        distro: str,
        port: int,
        log_path: Path,
        *,
        wsl_exe: str = WSL_EXE,
        runner: Runner = run_hidden,
    ) -> None:
        self.distro = distro
        self.port = port
        self._wsl = wsl_exe
        self._run = runner
        self._log = EngineLog(log_path)
        self._lock = threading.Lock()
        self._process: ManagedProcess | None = None
        self._release_job: Callable[[], None] | None = None
        self._keepalive: subprocess.Popen[bytes] | None = None
        self._keepalive_kill: Callable[[], None] | None = None
        self._keepalive_release: Callable[[], None] | None = None

    # --- commands -----------------------------------------------------------------------------

    def _base(self) -> list[str]:
        return [self._wsl, "-d", self.distro] if self.distro else [self._wsl]

    def spawn_command(self, engine_dir: str) -> list[str]:
        return [*self._base(), "-e", "bash", "-lc", engine_script(engine_dir, self.port)]

    def keepalive_command(self) -> list[str]:
        return [*self._base(), "-e", "sleep", "infinity"]

    def _quick(
        self, args: Sequence[str], timeout: float = QUICK_TIMEOUT_S
    ) -> subprocess.CompletedProcess[bytes] | None:
        try:
            return self._run(args, timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("%s failed: %s", " ".join(args[:4]), exc)
            return None

    # --- engine process ----------------------------------------------------------------------------

    def spawn(self, engine_dir: str, generation: int, on_exit: ExitCallback) -> None:
        command = self.spawn_command(engine_dir)
        self._log.write(f"[companion] starting: {' '.join(command)}")
        process, kill_tree, release = _spawn(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        def exited(gen: int, code: int, address_in_use: bool) -> None:
            if release is not None:
                release()
            on_exit(gen, code, address_in_use)

        managed = ManagedProcess(
            process, generation=generation, engine_log=self._log, on_exit=exited, kill_tree=kill_tree
        )
        with self._lock:
            self._process = managed
            self._release_job = release

    def owned(self) -> ManagedProcess | None:
        with self._lock:
            process = self._process
        return process if process is not None and process.running() else None

    def stop(self, client: DaemonClient | None, reason: ShutdownReason) -> None:
        process = self.owned()
        if process is None:
            return
        stop_gracefully(process, client, reason)

    # --- keepalive (attach mode) ------------------------------------------------------------------------

    def ensure_keepalive(self) -> None:
        with self._lock:
            if self._keepalive is not None and self._keepalive.poll() is None:
                return
        try:
            process, kill_tree, release = _spawn(
                self.keepalive_command(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            log.warning("keepalive failed to start: %s", exc)
            return
        with self._lock:
            self._keepalive = process
            self._keepalive_kill = kill_tree
            self._keepalive_release = release

    def stop_keepalive(self) -> None:
        with self._lock:
            process, self._keepalive = self._keepalive, None
            kill_tree, self._keepalive_kill = self._keepalive_kill, None
            release, self._keepalive_release = self._keepalive_release, None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if kill_tree is not None:
            kill_tree()
        if process.poll() is None:
            process.kill()
        try:
            process.wait(2.0)
        except subprocess.TimeoutExpired:
            pass
        if release is not None:
            release()

    # --- wsl.exe helpers --------------------------------------------------------------------------------

    def read_token(self) -> str | None:
        return read_wsl_token(self.distro, wsl_exe=self._wsl, runner=self._run)

    def systemd_unit_enabled(self) -> bool:
        result = self._quick([*self._base(), "-e", "systemctl", "--user", "is-enabled", SYSTEMD_UNIT])
        if result is None:
            return False
        return _text(result.stdout).strip() in ("enabled", "enabled-runtime")

    def detect_engine_dir(self) -> str | None:
        checks = " ".join(f'"{candidate}"' for candidate in ENGINE_DIR_CANDIDATES)
        script = (
            f"for d in {checks}; do "
            'if [ -f "$HOME/$d/Makefile" ] && [ -f "$HOME/$d/app/main.py" ]; then echo "$d"; exit 0; fi; '
            "done; exit 1"
        )
        result = self._quick([*self._base(), "-e", "sh", "-c", script])
        if result is None or result.returncode != 0:
            return None
        found = _text(result.stdout).strip().splitlines()
        return found[0].strip() if found else None

    def shutdown_wsl(self) -> None:
        self._log.write("[companion] wsl --shutdown")
        result = self._quick([self._wsl, "--shutdown"], SHUTDOWN_TIMEOUT_S)
        if result is not None and result.returncode != 0:
            log.warning("wsl --shutdown exited with %s: %s", result.returncode, _text(result.stderr).strip())
        # Whatever we started inside the VM is gone: drop the handles (and the jobs).
        process = self.owned()
        if process is not None:
            process.kill()
        self.stop_keepalive()

    def default_distro(self) -> str:
        result = self._quick([self._wsl, "-l", "-v"])
        return parse_default_distro(result.stdout) if result is not None else ""

    def other_running_distros(self) -> list[str]:
        result = self._quick([self._wsl, "-l", "-q", "--running"])
        if result is None or result.returncode != 0:
            return []
        ours = (self.distro or self.default_distro()).lower()
        return [name for name in parse_distro_list(result.stdout) if name.lower() != ours]

    def close(self) -> None:
        self.stop_keepalive()
        with self._lock:
            process, self._process = self._process, None
            release, self._release_job = self._release_job, None
        if process is not None and process.running():
            process.kill()
        if release is not None:
            release()
        self._log.close()
