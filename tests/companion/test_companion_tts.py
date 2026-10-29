"""Spoken-answers settings -> engine flags and environment, the provider probe and the test voice."""

from __future__ import annotations

import os
import threading
import wave
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from companion_core_fakes import use_english

from app.companion import tts
from app.companion.contract import CompanionSettings
from app.companion.settings_store import dump_settings, normalized, parse_settings, validate_settings
from app.companion.supervisor.backend import engine_script
from app.companion.supervisor.local import LocalBackend
from app.companion.supervisor.wsl import WslBackend
from app.companion.tts import EngineTts, engine_tts, probe_voices, spawn_env, try_voice
from app.protocol.tts_provider import ProviderClient, ProviderError, SpeechStream
from tools.examples.tts_provider_reference import ProviderServer, make_server

EXTERNAL = CompanionSettings(
    tts_engine="external", tts_url="http://127.0.0.1:9000", tts_voice="high", tts_token="s3cret"
)


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    use_english(monkeypatch)


@pytest.fixture
def provider() -> Iterator[ProviderServer]:
    server = make_server(pace=0.0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _url(server: ProviderServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


# --- flags and environment ---------------------------------------------------------------------------------


def test_the_default_engine_adds_no_flags_and_its_provider_fields_do_not_count() -> None:
    assert engine_tts(CompanionSettings()).cli_args() == ""
    edited = replace(CompanionSettings(), tts_url="http://other:1", tts_voice="x", tts_token="t")
    assert engine_tts(edited) == engine_tts(CompanionSettings())  # editing them must not restart the engine


def test_the_external_engine_adds_the_provider_flags_but_never_the_token() -> None:
    args = engine_tts(EXTERNAL).cli_args()
    assert args == "--tts-engine external --tts-url=http://127.0.0.1:9000 --tts-external-voice=high"
    assert "s3cret" not in args and "s3cret" not in repr(engine_tts(EXTERNAL))
    no_voice = engine_tts(replace(EXTERNAL, tts_voice=""))
    assert no_voice.cli_args() == "--tts-engine external --tts-url=http://127.0.0.1:9000"


def test_a_voice_id_that_starts_with_a_dash_still_parses_as_the_voice() -> None:
    from app.cli.args import parse_args

    args = engine_tts(replace(EXTERNAL, tts_voice="-1")).cli_args()
    assert parse_args(args.split()).tts_external_voice == "-1"


def test_engine_script_adds_the_tts_flags() -> None:
    script = engine_script("ai-lab/voice-mate", 47821, tts=engine_tts(EXTERNAL))
    assert script == (
        'cd "$HOME/ai-lab/voice-mate" && exec make run-engine ARGS="--daemon-port 47821 '
        '--tts-engine external --tts-url=http://127.0.0.1:9000 --tts-external-voice=high"'
    )
    assert "--tts" not in engine_script("ai-lab/voice-mate", 47821, tts=EngineTts("default"))
    assert "s3cret" not in script


def test_the_token_travels_in_the_environment_and_wsl_is_told_to_forward_it() -> None:
    assert spawn_env(engine_tts(CompanionSettings()), wsl=True) is None  # nothing to add: inherit
    assert spawn_env(engine_tts(replace(EXTERNAL, tts_token="")), wsl=True) is None
    env = spawn_env(engine_tts(EXTERNAL), wsl=True, base={"PATH": "x", "WSLENV": "FOO/p"})
    assert env is not None
    assert env["VOICEMATE_TTS_TOKEN"] == "s3cret" and env["WSLENV"] == "FOO/p:VOICEMATE_TTS_TOKEN"
    again = spawn_env(engine_tts(EXTERNAL), wsl=True, base=env)
    assert again is not None and again["WSLENV"] == "FOO/p:VOICEMATE_TTS_TOKEN"  # listed once
    local = spawn_env(engine_tts(EXTERNAL), wsl=False, base={"PATH": "x"})
    assert local == {"PATH": "x", "VOICEMATE_TTS_TOKEN": "s3cret"}


def test_the_backends_put_the_flags_on_the_command_and_not_the_token(tmp_path: Path) -> None:
    wsl = WslBackend("ai-lab", 47821, tmp_path / "engine.log", tts=engine_tts(EXTERNAL))
    command = " ".join(wsl.spawn_command("voice-mate"))
    assert "--tts-engine external" in command and "s3cret" not in command
    local = LocalBackend(47821, tmp_path / "engine.log", tts=engine_tts(EXTERNAL))
    assert "--tts-url=http://127.0.0.1:9000" in " ".join(local.spawn_command("voice-mate"))


# --- settings file ---------------------------------------------------------------------------------------


def test_settings_round_trip_keeps_the_provider_fields() -> None:
    result = parse_settings(dump_settings(EXTERNAL))
    assert (result.settings.tts_engine, result.settings.tts_url) == ("external", "http://127.0.0.1:9000")
    assert (result.settings.tts_voice, result.settings.tts_token) == ("high", "s3cret")
    assert not result.problems


def test_defaults_when_the_file_has_no_tts_keys() -> None:
    settings = parse_settings("version = 1\n").settings
    assert (settings.tts_engine, settings.tts_url, settings.tts_voice, settings.tts_token) == (
        "default",
        "http://127.0.0.1:8765",
        "",
        "",
    )


def test_bad_values_in_the_file_fall_back_to_the_defaults() -> None:
    text = 'version = 1\ntts_engine = "cloud"\ntts_url = "not a url"\ntts_voice = "a b"\ntts_token = "a\\nb"\n'
    result = parse_settings(text)
    defaults = CompanionSettings()
    assert (result.settings.tts_engine, result.settings.tts_url) == (defaults.tts_engine, defaults.tts_url)
    assert (result.settings.tts_voice, result.settings.tts_token) == ("", "")
    assert len(result.problems) >= 4


def test_token_is_not_in_the_repr_of_the_settings() -> None:
    assert "s3cret" not in repr(EXTERNAL)


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"tts_engine": "cloud"}, "Voice engine"),
        ({"tts_url": "ftp://x"}, "provider address"),
        ({"tts_url": 'http://h/"; rm'}, "provider address"),
        ({"tts_voice": "two words"}, "voice id"),
        ({"tts_voice": "$(x)"}, "voice id"),
        ({"tts_token": "a\nb"}, "token"),
    ],
)
def test_validation_refuses_what_cannot_travel_safely(changes: dict[str, str], fragment: str) -> None:
    errors = validate_settings(replace(EXTERNAL, **changes), check_files=False)  # type: ignore[arg-type]
    assert any(fragment.lower() in error.lower() for error in errors), errors


def test_valid_provider_settings_pass_and_an_empty_address_means_the_default() -> None:
    assert validate_settings(EXTERNAL, check_files=False) == []
    assert normalized(replace(EXTERNAL, tts_url="  ", tts_token=" t ")).tts_url == "http://127.0.0.1:8765"
    assert normalized(replace(EXTERNAL, tts_token=" t ")).tts_token == "t"


# --- the provider ---------------------------------------------------------------------------------------


def test_probe_voices_lists_what_the_provider_offers(provider: ProviderServer) -> None:
    probe = probe_voices(replace(EXTERNAL, tts_url=_url(provider)))
    assert probe.ok and probe.provider == "Reference test-tone provider"
    assert [voice.id for voice in probe.voices] == ["low", "mid", "high"]
    assert "Voices found: 3" in probe.message


def test_probe_voices_says_what_is_wrong() -> None:
    server = make_server()
    port = server.server_port
    server.server_close()
    down = probe_voices(replace(EXTERNAL, tts_url=f"http://127.0.0.1:{port}"))
    assert not down.ok and "Could not reach the provider" in down.message and not down.voices
    assert not probe_voices(replace(EXTERNAL, tts_url="nonsense")).ok


def test_a_wrong_token_is_reported_without_echoing_it() -> None:
    server = make_server(token="right")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        probe = probe_voices(replace(EXTERNAL, tts_url=_url(server), tts_token="wrong"))
        assert (
            not probe.ok
            and "refused the token" in probe.message
            and "wrong" not in probe.message.replace("refused", "")
        )
        assert probe_voices(replace(EXTERNAL, tts_url=_url(server), tts_token="right")).ok
    finally:
        server.shutdown()
        server.server_close()


def test_test_voice_writes_a_wav_and_plays_it(provider: ProviderServer, tmp_path: Path) -> None:
    played: list[Path] = []
    result = try_voice(replace(EXTERNAL, tts_url=_url(provider), tts_voice="low"), tmp_path, played.append)
    assert result.ok and "Playing a test sentence" in result.message
    assert len(played) == 1 and played[0].parent == tmp_path and played[0].suffix == ".wav"
    with wave.open(str(played[0]), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        assert wav.getnframes() > 24000 // 2
    assert provider.state.requests[0]["voice"] == "low"
    assert "Hello!" in provider.state.requests[0]["text"]


def test_test_voice_reports_an_unknown_voice_and_plays_nothing(provider: ProviderServer, tmp_path: Path) -> None:
    played: list[Path] = []
    result = try_voice(replace(EXTERNAL, tts_url=_url(provider), tts_voice="nope"), tmp_path, played.append)
    assert not result.ok and "does not know the voice 'nope'" in result.message
    assert played == []


def test_test_voice_refuses_audio_that_is_not_16_bit(
    provider: ProviderServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_speak = ProviderClient.speak

    def float_samples(self: ProviderClient, text: str, **kwargs: object) -> SpeechStream:
        stream = real_speak(self, text, **kwargs)  # type: ignore[arg-type]
        stream.audio_format = "pcm_f32le"  # the engine answered float samples to a 16-bit request
        return stream

    monkeypatch.setattr(ProviderClient, "speak", float_samples)
    played: list[Path] = []
    result = try_voice(replace(EXTERNAL, tts_url=_url(provider)), tmp_path, played.append)
    assert not result.ok and played == []  # noise is never written as a .wav


def test_test_voice_waits_for_nothing_when_the_provider_is_loading(tmp_path: Path) -> None:
    server = make_server(pace=0.0, loading_seconds=60)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = try_voice(replace(EXTERNAL, tts_url=_url(server)), tmp_path, lambda _path: None)
        assert not result.ok and "still loading" in result.message
    finally:
        server.shutdown()
        server.server_close()


def test_old_test_files_are_removed(provider: ProviderServer, tmp_path: Path) -> None:
    old = tmp_path / "tts-test-old.wav"
    old.write_bytes(b"x")
    stale = old.stat().st_mtime - tts.TEST_FILE_MAX_AGE_S - 5

    os.utime(old, (stale, stale))
    try_voice(replace(EXTERNAL, tts_url=_url(provider)), tmp_path, lambda _path: None)
    assert not old.exists()


def test_explain_covers_every_error_kind() -> None:
    for kind in ("unreachable", "unauthorized", "protocol", "busy", "not_ready", "rejected", "server"):
        assert tts.explain(ProviderError(kind, "detail"), "http://x")  # type: ignore[arg-type]
