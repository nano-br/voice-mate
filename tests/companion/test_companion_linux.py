"""The Linux desktop helpers. Pure Python, so they also run on Windows (tools are faked)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

from app.companion.linux import autostart, clipboard
from app.companion.linux.audio_devices import parse_sources
from app.companion.linux.sound import LinuxSound


def test_parse_sources_skips_monitors() -> None:
    output = (
        "56\talsa_output.pci.analog-stereo.monitor\tPipeWire\ts32le 2ch 48000Hz\tSUSPENDED\n"
        "57\talsa_input.pci.analog-stereo\tPipeWire\ts32le 2ch 48000Hz\tRUNNING\n"
        "58\tRDPSource\tmodule-rdp-source.c\ts16le 1ch 44100Hz\tSUSPENDED\n"
    )
    assert parse_sources(output) == 2
    assert parse_sources("") == 0


def test_desktop_entry_quoting(tmp_path: Path) -> None:
    command = ["/home/me/.venv/bin/python", "-m", "app.companion.main", "--autostart"]
    entry = autostart.desktop_entry(command)
    assert "Exec=/home/me/.venv/bin/python -m app.companion.main --autostart\n" in entry
    assert autostart.quote_exec_arg("/path with space/py") == '"/path with space/py"'
    assert autostart.quote_exec_arg('a"b$c') == '"a\\"b\\$c"'
    assert autostart.quote_exec_arg("100%") == "100%%"
    target = tmp_path / "autostart" / "voicemate-companion.desktop"
    assert not autostart.autostart_enabled(path=target)
    autostart.set_autostart(True, command, path=target)
    assert autostart.autostart_enabled(path=target)
    assert target.read_text(encoding="utf-8").startswith("[Desktop Entry]\n")
    autostart.set_autostart(False, command, path=target)
    assert not autostart.autostart_enabled(path=target)
    autostart.set_autostart(False, command, path=target)  # idempotent


def test_default_autostart_location_follows_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert autostart.desktop_file_path() == tmp_path / "autostart" / "voicemate-companion.desktop"


def _fake_tools(store: Path) -> tuple[list[str], list[str]]:
    copy = [sys.executable, "-c", f"import sys; open({str(store)!r}, 'wb').write(sys.stdin.buffer.read())"]
    paste = [sys.executable, "-c", f"import sys; sys.stdout.buffer.write(open({str(store)!r}, 'rb').read())"]
    return copy, paste


def test_clipboard_write_is_verified_by_reading_back(tmp_path: Path) -> None:
    store = tmp_path / "selection"
    assert clipboard.set_text_verified("olá\nmundo", _fake_tools(store))
    assert store.read_bytes().decode("utf-8") == "olá\nmundo"


def test_clipboard_write_fails_when_the_read_back_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "BACKOFF_S", 0.0)
    copy = [sys.executable, "-c", "import sys; sys.stdin.read()"]
    paste = [sys.executable, "-c", "print('something else')"]
    assert not clipboard.set_text_verified("text", (copy, paste))


def test_linux_clipboard_delivers_on_a_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools = _fake_tools(tmp_path / "selection")
    monkeypatch.setattr(clipboard, "clipboard_commands", lambda: tools)
    done = threading.Event()
    result: list[bool] = []

    def finished(ok: bool) -> None:
        result.append(ok)
        done.set()

    clipboard.LinuxClipboard().deliver("hi", finished)
    assert done.wait(10)
    assert result == [True]


def test_sound_without_a_player_is_silent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    player = LinuxSound()
    assert not player.available
    player.play(tmp_path / "x.wav")
    player.stop()
