"""Vendor/backend/flow resolution in build_config (precedence CLI > saved > default)."""

from __future__ import annotations

from app.cli.args import parse_args
from app.cli.config_builder import build_config
from app.setup.persisted_config import PersistedConfig


def test_cpu_flag_forces_cpu_faster_whisper() -> None:
    config = build_config(parse_args(["--cpu"]))
    assert config.gpu_vendor == "cpu"
    assert config.whisper_backend == "faster-whisper"
    assert config.use_cpu is True


def test_gpu_backend_amd_defaults_to_whispercpp() -> None:
    config = build_config(parse_args(["--gpu-backend", "amd"]))
    assert config.gpu_vendor == "amd"
    assert config.whisper_backend == "whispercpp"


def test_gpu_backend_nvidia_defaults_to_faster_whisper() -> None:
    config = build_config(parse_args(["--gpu-backend", "nvidia"]))
    assert config.gpu_vendor == "nvidia"
    assert config.whisper_backend == "faster-whisper"


def test_persisted_vendor_used_without_flags() -> None:
    config = build_config(parse_args([]), PersistedConfig(gpu_vendor="amd"))
    assert config.gpu_vendor == "amd"
    assert config.whisper_backend == "whispercpp"


def test_cli_flag_overrides_persisted_vendor() -> None:
    config = build_config(parse_args(["--gpu-backend", "cpu"]), PersistedConfig(gpu_vendor="amd"))
    assert config.gpu_vendor == "cpu"


def test_whisper_backend_flag_overrides_default() -> None:
    config = build_config(parse_args(["--gpu-backend", "nvidia", "--whisper-backend", "openai-whisper"]))
    assert config.whisper_backend == "openai-whisper"


def test_persisted_whisper_backend_respected() -> None:
    config = build_config(parse_args(["--gpu-backend", "nvidia"]), PersistedConfig(whisper_backend="openai-whisper"))
    assert config.whisper_backend == "openai-whisper"


def test_gpu_vendor_propagates_to_tts_config() -> None:
    config = build_config(parse_args(["--gpu-backend", "amd"]))
    assert config.tts.gpu_vendor == "amd"


def test_persisted_tts_disabled_respected() -> None:
    config = build_config(parse_args([]), PersistedConfig(gpu_vendor="cpu", tts_enabled=False))
    assert config.tts.enabled is False


def test_persisted_clipboard_flow_disables_claude() -> None:
    config = build_config(parse_args([]), PersistedConfig(gpu_vendor="cpu", default_flow="clipboard"))
    assert config.claude_chat_enabled is False


def test_no_tts_flag_overrides_persisted_enabled() -> None:
    config = build_config(parse_args(["--no-tts"]), PersistedConfig(gpu_vendor="cpu", tts_enabled=True))
    assert config.tts.enabled is False
