"""Resolução das novas opções realtime no build_config."""

from __future__ import annotations

from app.cli.args import parse_args
from app.cli.config_builder import build_config
from app.setup.persisted_config import PersistedConfig


def test_transcription_language_defaults_to_pt_from_output_lang() -> None:
    # output_lang default = pt-BR → pt
    config = build_config(parse_args([]))
    assert config.transcription_language == "pt"


def test_transcription_language_derived_from_output_lang_en() -> None:
    config = build_config(parse_args(["--output-lang", "en"]))
    assert config.transcription_language == "en"


def test_transcription_language_derived_strips_region() -> None:
    config = build_config(parse_args(["--output-lang", "es-ES"]))
    assert config.transcription_language == "es"


def test_transcription_language_unknown_falls_back_to_auto() -> None:
    config = build_config(parse_args(["--output-lang", "tlh"]))
    assert config.transcription_language == "auto"


def test_explicit_transcription_language_overrides_output_lang() -> None:
    config = build_config(parse_args(["--output-lang", "en", "--transcription-language", "pt"]))
    assert config.transcription_language == "pt"


def test_transcription_language_auto_is_respected() -> None:
    config = build_config(parse_args(["--transcription-language", "auto"]))
    assert config.transcription_language == "auto"


def test_platform_trigger_flags_override_persisted() -> None:
    args = parse_args(
        ["--platform", "wsl2", "--trigger", "socket", "--daemon-port", "50000", "--stt-strategy", "whispercpp"]
    )
    persisted = PersistedConfig(platform="linux-x11", trigger="pynput", daemon_port=47821, stt_strategy="auto")
    config = build_config(args, persisted)
    assert config.platform == "wsl2"
    assert config.trigger == "socket"
    assert config.daemon_port == 50000
    assert config.stt_strategy == "whispercpp"


def test_platform_defaults_come_from_persisted() -> None:
    persisted = PersistedConfig(
        platform="wsl2",
        trigger="socket",
        daemon_port=48000,
        stt_strategy="faster-whisper-rocm",
        ct2_rocm_ok=True,
    )
    config = build_config(parse_args([]), persisted)
    assert config.platform == "wsl2"
    assert config.trigger == "socket"
    assert config.daemon_port == 48000
    assert config.stt_strategy == "faster-whisper-rocm"
    assert config.ct2_rocm_ok is True


def test_platform_unset_stays_none_for_auto_detect() -> None:
    config = build_config(parse_args([]), PersistedConfig())
    assert config.platform is None  # main.py resolve via detect_platform()
    assert config.trigger is None
    assert config.daemon_port == 47821
    assert config.stt_strategy == "auto"
    assert config.ct2_rocm_ok is None


def test_whispercpp_mode_defaults_to_server() -> None:
    config = build_config(parse_args([]))
    assert config.whispercpp_mode == "server"


def test_whispercpp_mode_cli_flag() -> None:
    config = build_config(parse_args(["--whispercpp-mode", "cli"]))
    assert config.whispercpp_mode == "cli"


def test_tts_engine_defaults_to_omnivoice() -> None:
    config = build_config(parse_args([]))
    assert config.tts.engine == "omnivoice"


def test_tts_engine_voxcpm_flag() -> None:
    config = build_config(parse_args(["--tts-engine", "voxcpm"]))
    assert config.tts.engine == "voxcpm"


def test_claude_model_defaults_to_haiku() -> None:
    config = build_config(parse_args([]))
    assert config.claude_chat.model == "claude-haiku-4-5"


def test_claude_model_override() -> None:
    config = build_config(parse_args(["--claude-model", "claude-sonnet-4-6"]))
    assert config.claude_chat.model == "claude-sonnet-4-6"


def test_tts_language_defaults_to_pt_from_output_lang() -> None:
    config = build_config(parse_args([]))
    assert config.tts.language == "pt"


def test_tts_language_follows_output_lang() -> None:
    config = build_config(parse_args(["--output-lang", "en"]))
    assert config.tts.language == "en"
