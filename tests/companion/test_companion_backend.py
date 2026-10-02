from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest
from companion_core_fakes import FakeDaemon, wait_until

from app.companion.client import DaemonClient
from app.companion.contract import CompanionSettings
from app.companion.controller import default_backend
from app.companion.desktop import autostart_command
from app.companion.dictation import EngineLanguage
from app.companion.supervisor import backend as backend_module
from app.companion.supervisor.backend import (
    EngineLog,
    ExternalBackend,
    ManagedProcess,
    engine_script,
    read_local_token,
    stop_gracefully,
)
from app.companion.supervisor.local import LocalBackend
from app.companion.supervisor.wsl import WslBackend, parse_default_distro, parse_distro_list, read_wsl_token


def test_autostart_from_source_works_from_any_working_directory(tmp_path: Path) -> None:
    """At login the working directory is not the checkout: `-m app.companion.main` alone
    would fail with ModuleNotFoundError."""
    command = autostart_command()
    assert command[-1] == "--autostart" and command[1] == "-c"
    probe = command[2].replace("app.companion.main", "app.companion.chords")  # main.py is the UI's
    result = subprocess.run(
        [sys.executable, "-c", probe, "--autostart"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_engine_script_quotes_the_folder_without_expanding_tilde() -> None:
    assert engine_script("ai-lab/voice-mate", 47821) == (
        'cd "$HOME/ai-lab/voice-mate" && exec make run-engine ARGS="--daemon-port 47821"'
    )
    assert (
        engine_script("/opt/voice mate", 5000)
        == 'cd "/opt/voice mate" && exec make run-engine ARGS="--daemon-port 5000"'
    )


def test_engine_script_passes_the_dictation_language_flags() -> None:
    portuguese = EngineLanguage("pt", "pt-BR")
    assert engine_script("ai-lab/voice-mate", 47821, portuguese) == (
        'cd "$HOME/ai-lab/voice-mate" && exec make run-engine '
        'ARGS="--daemon-port 47821 --transcription-language pt --output-lang pt-BR"'
    )
    assert engine_script("/opt/voice mate", 5000, EngineLanguage("auto", "zh-CN")) == (
        'cd "/opt/voice mate" && exec make run-engine '
        'ARGS="--daemon-port 5000 --transcription-language auto --output-lang zh-CN"'
    )


def test_wsl_spawn_command_carries_the_language(tmp_path: Path) -> None:
    wsl = WslBackend("ai-lab", 47999, tmp_path / "engine.log", language=EngineLanguage("en", "en"))
    assert wsl.spawn_command("ai-lab/voice-mate") == [
        "wsl.exe",
        "-d",
        "ai-lab",
        "-e",
        "bash",
        "-lc",
        'cd "$HOME/ai-lab/voice-mate" && exec make run-engine '
        'ARGS="--daemon-port 47999 --transcription-language en --output-lang en"',
    ]


def test_local_spawn_command_carries_the_language(tmp_path: Path) -> None:
    local = LocalBackend(47999, tmp_path / "engine.log", language=EngineLanguage("ru", "ru"))
    assert local.spawn_command("ai-lab/voice-mate") == [
        "bash",
        "-lc",
        'cd "$HOME/ai-lab/voice-mate" && exec make run-engine '
        'ARGS="--daemon-port 47999 --transcription-language ru --output-lang ru"',
    ]


def test_default_backend_resolves_the_setting_for_each_spawning_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.companion.dictation.catalog_for", lambda language: "es")  # a Spanish OS
    wsl = default_backend(CompanionSettings(engine_mode="wsl2", dictation_language="zh"), tmp_path)
    assert isinstance(wsl, WslBackend)
    assert wsl.spawn_command("x")[-1].endswith(
        'ARGS="--daemon-port 47821 --transcription-language zh --output-lang zh-CN"'
    )
    local = default_backend(CompanionSettings(engine_mode="local"), tmp_path)  # follows the interface (Spanish)
    assert isinstance(local, LocalBackend)
    assert local.spawn_command("x")[-1].endswith(
        'ARGS="--daemon-port 47821 --transcription-language es --output-lang es"'
    )
    wsl.close()
    local.close()


class FakeRunner:
    def __init__(self, replies: dict[str, tuple[int, bytes]]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(list(args))
        joined = " ".join(args)
        for needle, (code, out) in self.replies.items():
            if needle in joined:
                return subprocess.CompletedProcess(list(args), code, out, b"")
        return subprocess.CompletedProcess(list(args), 1, b"", b"")


def test_wsl_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_API_TOKEN", raising=False)
    runner = FakeRunner(
        {
            "api-token": (0, b"tok-123\n"),
            "is-enabled": (0, b"enabled\n"),
            "for d in": (0, b"workspace/voice-mate\n"),
            "--running": (0, b"ai-lab\r\ndocker-desktop\r\n"),
            "-l -v": (0, b"  NAME   STATE   VERSION\r\n* Ubuntu  Running  2\r\n  ai-lab  Running 2\r\n"),
            "--shutdown": (0, b""),
        }
    )
    wsl = WslBackend("ai-lab", 47999, tmp_path / "engine.log", wsl_exe="wsl.exe", runner=runner)
    assert wsl.spawn_command("ai-lab/voice-mate") == [
        "wsl.exe",
        "-d",
        "ai-lab",
        "-e",
        "bash",
        "-lc",
        'cd "$HOME/ai-lab/voice-mate" && exec make run-engine ARGS="--daemon-port 47999"',
    ]
    assert wsl.keepalive_command() == ["wsl.exe", "-d", "ai-lab", "-e", "sleep", "infinity"]
    assert wsl.read_token() == "tok-123"
    assert runner.calls[-1] == ["wsl.exe", "-d", "ai-lab", "-e", "sh", "-c", 'cat "$HOME/.config/voicemate/api-token"']
    assert wsl.systemd_unit_enabled()
    assert wsl.detect_engine_dir() == "workspace/voice-mate"
    assert wsl.other_running_distros() == ["docker-desktop"]
    wsl.shutdown_wsl()
    assert ["wsl.exe", "--shutdown"] in runner.calls
    wsl.close()
    assert "wsl --shutdown" in (tmp_path / "engine.log").read_text(encoding="utf-8")

    default = WslBackend("", 47999, tmp_path / "e2.log", runner=runner)
    assert default.spawn_command("x")[:2] == ["wsl.exe", "-e"]
    assert default.other_running_distros() == ["ai-lab", "docker-desktop"]  # ours is the default, Ubuntu


def test_wsl_failures_are_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_API_TOKEN", raising=False)

    def broken(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("wsl.exe")

    wsl = WslBackend("ai-lab", 1, tmp_path / "engine.log", runner=broken)
    assert wsl.read_token() is None
    assert not wsl.systemd_unit_enabled()
    assert wsl.detect_engine_dir() is None
    assert wsl.other_running_distros() == []
    assert read_wsl_token("ai-lab", runner=broken) is None
    monkeypatch.setenv("VOICEMATE_API_TOKEN", "from-env")
    assert wsl.read_token() == "from-env"


def test_distro_list_parsing_handles_utf16() -> None:
    utf16 = "Ubuntu\r\nai-lab\r\n".encode("utf-16-le")
    assert parse_distro_list(utf16) == ["Ubuntu", "ai-lab"]
    assert parse_default_distro("  NAME STATE\r\n* ai-lab Running 2\r\n".encode("utf-16-le")) == "ai-lab"
    assert parse_default_distro(b"nothing") == ""


def _python_child(code: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )


def test_managed_process_drains_output_and_reports_the_exit(tmp_path: Path) -> None:
    exits: list[tuple[int, int, bool]] = []
    log = EngineLog(tmp_path / "logs" / "engine.log")
    assert not (tmp_path / "logs").exists()  # created on the first line only
    code = "import sys\nfor i in range(2000): print('line', i)\nprint('OSError: [Errno 98] Address already in use')\nsys.exit(3)"
    managed = ManagedProcess(_python_child(code), generation=7, engine_log=log, on_exit=lambda *a: exits.append(a))
    assert wait_until(lambda: exits, timeout=10)
    assert exits == [(7, 3, True)]
    log.close()
    text = (tmp_path / "logs" / "engine.log").read_text(encoding="utf-8")
    assert "line 1999" in text and "exited with code 3" in text
    assert not managed.running()


def test_stop_gracefully_uses_stdin_eof(tmp_path: Path) -> None:
    code = "import sys\nsys.stdin.read()\nprint('eof seen')"
    managed = ManagedProcess(_python_child(code), generation=1, engine_log=EngineLog(tmp_path / "e.log"), on_exit=None)
    stop_gracefully(managed, None, "user_quit")
    assert not managed.running()
    assert wait_until(lambda: "eof seen" in managed.tail())


def test_stop_gracefully_kills_a_process_that_ignores_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend_module, "SHUTDOWN_WAIT_S", 0.3)
    killed: list[bool] = []
    process = _python_child("import time\ntime.sleep(60)")
    managed = ManagedProcess(
        process,
        generation=1,
        engine_log=EngineLog(tmp_path / "e.log"),
        on_exit=None,
        kill_tree=lambda: killed.append(True),
    )
    stop_gracefully(managed, None, "user_quit")
    assert killed == [True]
    assert not managed.running()


def test_stop_gracefully_asks_the_daemon_first(tmp_path: Path) -> None:
    daemon = FakeDaemon().start()
    try:
        code = "import time\ntime.sleep(60)"
        managed = ManagedProcess(
            _python_child(code), generation=1, engine_log=EngineLog(tmp_path / "e.log"), on_exit=None
        )
        threading.Timer(0.3, managed.process.kill).start()  # "the daemon exits after /shutdown"
        stop_gracefully(managed, DaemonClient(daemon.port), "restart")
        assert daemon.shutdowns == ["restart"]
        assert not managed.running()
    finally:
        daemon.stop()


def test_local_backend_detects_the_engine_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    local = LocalBackend(1, tmp_path / "e.log")
    assert local.detect_engine_dir() is None
    root = tmp_path / "ai-lab" / "voice-mate"
    (root / "app").mkdir(parents=True)
    (root / "Makefile").write_text("x", encoding="utf-8")
    (root / "app" / "main.py").write_text("x", encoding="utf-8")
    assert local.detect_engine_dir() == "ai-lab/voice-mate"
    assert local.spawn_command("ai-lab/voice-mate")[-1].startswith('cd "$HOME/ai-lab/voice-mate"')


def test_local_token_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("VOICEMATE_API_TOKEN", raising=False)
    assert read_local_token() is None
    token_file = tmp_path / ".config" / "voicemate" / "api-token"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("abc\n", encoding="utf-8")
    assert read_local_token() == "abc"
    assert LocalBackend(1, tmp_path / "e.log").read_token() == "abc"
    external = ExternalBackend()
    assert external.read_token() == "abc"
    assert not external.can_spawn
    with pytest.raises(OSError):
        external.spawn("x", 1, lambda *a: None)


@pytest.mark.skipif(not sys.platform.startswith("linux") or shutil.which("make") is None, reason="needs bash and make")
def test_local_backend_runs_make_run_engine(tmp_path: Path) -> None:
    engine = tmp_path / "engine"
    engine.mkdir()
    (engine / "Makefile").write_text("run-engine:\n\t@echo started $(ARGS)\n", encoding="utf-8")
    local = LocalBackend(48123, tmp_path / "engine.log")
    exits: list[tuple[int, int, bool]] = []
    local.spawn(str(engine), 1, lambda *a: exits.append(a))
    assert wait_until(lambda: exits, timeout=10)
    assert exits == [(1, 0, False)]
    local.close()
    assert "started --daemon-port 48123" in (tmp_path / "engine.log").read_text(encoding="utf-8")
