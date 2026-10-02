"""The ready banner: supervised by the companion, no hints about hotkey scripts or Ctrl+C."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from app.core.config import Config, FlowConfig
from app.daemon.server import ApiServer
from app.main import _print_ready

FLOWS = [FlowConfig(name="clipboard", kind="clipboard", hotkey="ctrl+alt+v")]
API = cast(ApiServer, SimpleNamespace(port=47821))
WSL2 = Config(platform="wsl2", trigger="socket")


def test_standalone_daemon_points_to_the_hotkey_scripts(capsys: pytest.CaptureFixture[str]) -> None:
    _print_ready(WSL2, FLOWS, API, supervised=False)
    out = capsys.readouterr().out
    assert "http://127.0.0.1:47821" in out
    assert "voicemate-hotkeys.ahk" in out
    assert "Ctrl+C" in out


def test_supervised_daemon_leaves_the_script_and_ctrl_c_hints_out(capsys: pytest.CaptureFixture[str]) -> None:
    _print_ready(WSL2, FLOWS, API, supervised=True)
    out = capsys.readouterr().out
    assert "http://127.0.0.1:47821" in out
    assert "voicemate-hotkeys" not in out
    assert "Ctrl+C" not in out


def test_supervised_local_engine_keeps_its_hotkeys_and_drops_ctrl_c(capsys: pytest.CaptureFixture[str]) -> None:
    """Linux `local` mode: the engine itself holds the hotkeys, so they stay in the banner."""
    _print_ready(Config(platform="linux-x11", trigger="pynput"), FLOWS, API, supervised=True)
    out = capsys.readouterr().out
    assert "ctrl+alt+v" in out
    assert "http://127.0.0.1:47821" in out
    assert "Ctrl+C" not in out
