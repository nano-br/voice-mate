from __future__ import annotations

import tomllib
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from companion_core_fakes import use_english

from app.companion.contract import CUE_NAMES, SETTINGS_VERSION, CompanionSettings, CueSettings, HotkeyBinding
from app.companion.settings_store import (
    SettingsStore,
    dump_settings,
    engine_dir_is_valid,
    normalized,
    parse_settings,
    validate_settings,
)


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    use_english(monkeypatch)


def _wav(path: Path, *, width: int = 2, seconds: float = 0.1, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(b"\x00" * width * int(rate * seconds))
    return path


def test_round_trip_keeps_every_field(tmp_path: Path) -> None:
    cues = {name: CueSettings() for name in CUE_NAMES}
    cues["ready"] = CueSettings(enabled=False, source="file", preset="soft", file='C:\\Sounds\\a "b".wav', volume=0.25)
    settings = CompanionSettings(
        client_key="abcdef0123456789",
        language="pt-BR",
        engine_mode="external",
        wsl_distro="ai-lab",
        engine_dir="/opt/voice mate",
        daemon_port=48000,
        hotkeys=(HotkeyBinding("clipboard", "ctrl+alt+v"), HotkeyBinding("claude_chat", "ctrl+shift+f9")),
        cues_enabled=False,
        master_volume=0.55,
        cues=cues,
        wsl_restart_policy="ask",
        notify_level="errors",
        start_at_login=True,
    )
    text = dump_settings(settings)
    tomllib.loads(text)  # valid TOML
    result = parse_settings(text)
    assert result.problems == ()
    assert not result.read_only
    assert result.settings == settings


def test_layout_matches_the_documented_file() -> None:
    text = dump_settings(CompanionSettings(client_key="k" * 16, engine_mode="wsl2"))
    lines = text.splitlines()
    assert lines[0] == f"version = {SETTINGS_VERSION}"
    assert 'engine_mode = "wsl2"' in lines
    assert "[[hotkeys]]" in lines
    assert "[cues.start]" in lines and "[cues.ai_ready]" in lines
    assert lines.index("[[hotkeys]]") > lines.index("start_at_login = false")


def test_empty_hotkeys_survive_a_round_trip() -> None:
    settings = CompanionSettings(client_key="k" * 16, hotkeys=())
    assert parse_settings(dump_settings(settings)).settings.hotkeys == ()


def test_invalid_values_fall_back_to_defaults_with_log_lines() -> None:
    text = "\n".join(
        [
            "version = 1",
            'client_key = "bad key with spaces"',
            'language = "klingon"',
            'engine_mode = "docker"',
            'engine_dir = "ai-lab/$(rm -rf ~)"',
            'wsl_distro = "bad name"',
            "daemon_port = 80",
            "master_volume = 3",
            'notify_level = "loud"',
            "start_at_login = 1",
            "mystery = true",
            "[[hotkeys]]",
            'flow = "clipboard"',
            'chord = "shift+a"',
            "[[hotkeys]]",
            'flow = "claude_chat"',
            'chord = "ctrl+alt+a"',
            "[cues.start]",
            'source = "radio"',
            "volume = -1",
            "[cues.nope]",
            "enabled = false",
        ]
    )
    result = parse_settings(text)
    defaults = CompanionSettings()
    settings = result.settings
    assert settings.client_key == ""
    assert settings.language == defaults.language
    assert settings.engine_mode == defaults.engine_mode
    assert settings.engine_dir == ""
    assert settings.wsl_distro == ""
    assert settings.daemon_port == defaults.daemon_port
    assert settings.master_volume == defaults.master_volume
    assert settings.notify_level == defaults.notify_level
    assert settings.start_at_login is False
    assert settings.hotkeys == (HotkeyBinding("claude_chat", "ctrl+alt+a"),)
    assert settings.cues["start"] == CueSettings()
    assert len(result.problems) >= 12
    assert any("mystery" in problem for problem in result.problems)


def test_broken_toml_gives_defaults() -> None:
    result = parse_settings("this is = = not toml")
    assert result.settings == CompanionSettings()
    assert result.problems and result.broken


@pytest.mark.parametrize("content", [b"this is = = not toml", b'language = "\xff\xfe"\n'])
def test_a_broken_file_is_moved_aside_never_overwritten(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "companion.toml"
    path.write_bytes(content)
    store = SettingsStore(path)
    backup = tmp_path / "companion.toml.broken"
    assert store.broken_backup == backup
    assert backup.read_bytes() == content  # the user's file survives
    assert store.get().client_key  # fresh defaults, written to a new file
    assert parse_settings(path.read_text(encoding="utf-8")).problems == ()


def test_a_valid_file_is_not_moved(tmp_path: Path) -> None:
    path = tmp_path / "companion.toml"
    path.write_text('client_key = "abcdefgh12345678"\nlanguage = "klingon"\n', encoding="utf-8")
    store = SettingsStore(path)
    assert store.broken_backup is None
    assert not (tmp_path / "companion.toml.broken").exists()


@pytest.mark.parametrize(
    ("stored", "platform", "expected"),
    [
        ("local", "win32", "wsl2"),
        ("wsl2", "linux", "local"),
        ("external", "win32", "external"),
        ("external", "linux", "external"),
        ("local", "linux", "local"),
    ],
)
def test_engine_mode_must_exist_on_the_platform(stored: str, platform: str, expected: str) -> None:
    result = parse_settings(f'engine_mode = "{stored}"\n', platform)
    assert result.settings.engine_mode == expected
    assert bool(result.problems) == (stored != expected)
    errors = validate_settings(CompanionSettings(engine_mode=stored), platform=platform)  # type: ignore[arg-type]
    assert ("This engine mode is not available on this system." in errors) == (stored != expected)


def test_validation_names_settings_with_localized_labels() -> None:
    cues = {name: CueSettings() for name in CUE_NAMES}
    cues["start"] = CueSettings(source="radio")  # type: ignore[arg-type]
    errors = validate_settings(CompanionSettings(cues=cues, language="tlh"))  # type: ignore[arg-type]
    assert "Invalid value for Recording started." in errors
    assert "Invalid value for Language." in errors


def test_newer_version_is_read_only(tmp_path: Path) -> None:
    path = tmp_path / "companion.toml"
    path.write_text('version = 99\nclient_key = "abcdefgh12345678"\nlanguage = "es"\n', encoding="utf-8")
    store = SettingsStore(path)
    assert store.read_only
    assert store.get().language == "es"
    with pytest.raises(PermissionError):
        store.save(store.get())
    assert "version = 99" in path.read_text(encoding="utf-8")


def test_store_generates_and_persists_a_client_key(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "companion.toml"
    store = SettingsStore(path)
    key = store.get().client_key
    assert len(key) == 32
    assert path.is_file()
    assert SettingsStore(path).get().client_key == key


def test_save_keeps_the_client_key_and_writes_lf(tmp_path: Path) -> None:
    path = tmp_path / "companion.toml"
    store = SettingsStore(path)
    key = store.get().client_key
    store.save(replace(store.get(), client_key="", language="es"))
    assert store.get().client_key == key
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert SettingsStore(path).get().language == "es"


@pytest.mark.parametrize("bad", ['a"b', "a$b", "a`b", "a\nb", "a\rb", "a\\b", "ai-lab\\"])
def test_engine_dir_rejects_shell_breaking_characters(bad: str) -> None:
    assert not engine_dir_is_valid(bad)
    errors = validate_settings(CompanionSettings(engine_dir=bad))
    assert any("engine folder" in error for error in errors)


def test_engine_dir_accepts_spaces_and_quotes_inside_the_double_quotes() -> None:
    assert engine_dir_is_valid("my projects/voice-mate")
    assert engine_dir_is_valid("it's/here")
    assert validate_settings(CompanionSettings(engine_dir="my projects/voice-mate")) == []


def test_validate_reports_hotkey_problems() -> None:
    settings = CompanionSettings(
        hotkeys=(
            HotkeyBinding("clipboard", "ctrl+alt+v"),
            HotkeyBinding("claude_chat", "Alt+Ctrl+V"),
            HotkeyBinding("other", "f12"),
            HotkeyBinding("clipboard", "ctrl+alt+b"),
        )
    )
    errors = validate_settings(settings)
    assert "Ctrl+Alt+V is assigned to more than one action." in errors
    assert "f12 is not a valid hotkey." in errors
    assert "The action clipboard has more than one hotkey." in errors


def test_validate_ranges_and_literals() -> None:
    errors = validate_settings(
        CompanionSettings(daemon_port=22, master_volume=1.5, wsl_distro="no spaces allowed", notify_level="x")  # type: ignore[arg-type]
    )
    assert "The port must be between 1024 and 65535." in errors
    assert "The volume must be between 0 and 1." in errors
    assert "The WSL distribution name is not valid." in errors
    assert "Invalid value for Notifications." in errors


def test_validate_custom_cue_files(tmp_path: Path) -> None:
    good = _wav(tmp_path / "good.wav")
    eight = _wav(tmp_path / "eight.wav", width=1)
    long = _wav(tmp_path / "long.wav", seconds=11, rate=1000)
    wide = _wav(tmp_path / "wide.wav", width=3)
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"not a wav at all")

    def errors_for(path: Path) -> list[str]:
        cues = {name: CueSettings() for name in CUE_NAMES}
        cues["error"] = CueSettings(source="file", file=str(path))
        return validate_settings(CompanionSettings(cues=cues))

    assert errors_for(good) == []
    assert errors_for(eight) == []
    assert errors_for(long) == ["The sound file for the Error cue is longer than 10 seconds."]
    assert errors_for(wide) == ["The sound file for the Error cue must be a PCM 8 or 16-bit WAV file."]
    assert errors_for(junk) == ["The sound file for the Error cue must be a PCM 8 or 16-bit WAV file."]
    assert errors_for(tmp_path / "missing.wav") == ["The sound file for the Error cue was not found."]


def test_normalized_cleans_chords_and_home_prefix() -> None:
    settings = normalized(
        CompanionSettings(engine_dir="~/ai-lab/voice-mate", hotkeys=(HotkeyBinding("clipboard", "Alt+Ctrl+V"),))
    )
    assert settings.engine_dir == "ai-lab/voice-mate"
    assert settings.hotkeys == (HotkeyBinding("clipboard", "ctrl+alt+v"),)
