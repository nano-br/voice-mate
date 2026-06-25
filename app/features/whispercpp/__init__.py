"""Whisper via whisper.cpp + Vulkan (native binary, vendor-agnostic).

It's the preferred transcription backend on AMD: runs the quantized large-v3-turbo
(GGUF) on the GPU via Vulkan with ~0.64 GB of VRAM (vs ~4.8 GB for openai-whisper)
and equivalent speed — without depending on torch ROCm or CTranslate2 (which has
no ROCm and hangs on gfx1201).

It's not a pip package: it uses `whisper-cli.exe` + Vulkan DLLs + a GGUF model
that `make setup` downloads to `~/.cache/voicemate/whispercpp/`. That's why
`is_available()` takes the config (it must check files on disk, not imports).
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.config import Config
from app.core.transcription_backend import TranscriptionBackend
from app.i18n import _

__all__ = ["build_backend", "default_dir", "find_vad_model", "is_available", "resolve_dir"]

_DIR_NAME = "whispercpp"
_EXE_NAMES = ("whisper-cli.exe", "whisper-cli")
_SERVER_EXE_NAMES = ("whisper-server.exe", "whisper-server")


def default_dir() -> Path:
    return Path.home() / ".cache" / "voicemate" / _DIR_NAME


def resolve_dir(config: Config) -> Path:
    return Path(config.whispercpp_dir) if config.whispercpp_dir else default_dir()


def _find_first(directory: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def find_exe(directory: Path) -> Path | None:
    return _find_first(directory, _EXE_NAMES)


def find_server_exe(directory: Path) -> Path | None:
    return _find_first(directory, _SERVER_EXE_NAMES)


def find_model(directory: Path) -> Path | None:
    """Transcription model (excludes the VAD model, which is also ggml-*.bin)."""
    models = sorted(
        path for path in directory.glob("ggml-*.bin") if "silero" not in path.name and "vad" not in path.name
    )
    return models[0] if models else None


def find_vad_model(directory: Path) -> Path | None:
    """silero-VAD model (silence-based trimming — avoids cutting mid-word)."""
    models = sorted(path for path in directory.glob("ggml-*.bin") if "silero" in path.name or "vad" in path.name)
    return models[0] if models else None


def is_available(config: Config) -> bool:
    """True if there's a GGUF model and at least one binary (server for server mode, cli otherwise)."""
    directory = resolve_dir(config)
    if find_model(directory) is None:
        return False
    if config.whispercpp_mode == "server":
        # server falls back to cli if whisper-server.exe doesn't exist, so either works.
        return find_server_exe(directory) is not None or find_exe(directory) is not None
    return find_exe(directory) is not None


def _build_cli_backend(config: Config, directory: Path, model: Path) -> TranscriptionBackend:
    exe = find_exe(directory)
    if exe is None:
        raise FileNotFoundError(f"whisper-cli not found in {directory} (run `make configure`).")
    from app.features.whispercpp.backend import WhisperCppBackend

    return WhisperCppBackend(config, exe, model)


def build_backend(config: Config) -> TranscriptionBackend:
    directory = resolve_dir(config)
    model = find_model(directory)
    if model is None:
        raise FileNotFoundError(f"whisper.cpp GGUF model not found in {directory} (run `make configure`).")

    if config.whispercpp_mode == "server":
        server_exe = find_server_exe(directory)
        if server_exe is not None:
            from app.features.whispercpp.server_backend import WhisperCppServerBackend

            try:
                return WhisperCppServerBackend(config, server_exe, model)
            except Exception as exc:  # noqa: BLE001 — any startup failure falls back to cli
                print(
                    _("[VoiceMate] ⚠ whisper-server failed to start ({exc}); falling back to whisper-cli.").format(
                        exc=exc
                    ),
                    file=sys.stderr,
                )
        else:
            print(
                _("[VoiceMate] ⚠ whisper-server.exe not found; using whisper-cli (cli mode)."),
                file=sys.stderr,
            )

    return _build_cli_backend(config, directory, model)
