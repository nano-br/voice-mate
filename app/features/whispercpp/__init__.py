"""Whisper via whisper.cpp + Vulkan (binário nativo, vendor-agnóstico).

É o backend de transcrição preferido na AMD: roda o large-v3-turbo quantizado
(GGUF) na GPU via Vulkan com ~0,64 GB de VRAM (vs ~4,8 GB do openai-whisper) e
velocidade equivalente — sem depender do torch ROCm nem do CTranslate2 (que não
tem ROCm e trava na gfx1201).

Não é um pacote pip: usa o `whisper-cli.exe` + DLLs Vulkan + um modelo GGUF que
o `make setup` baixa para `~/.cache/voicemate/whispercpp/`. Por isso o
`is_available()` recebe o config (precisa checar arquivos no disco, não imports).
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Config
from app.core.transcription_backend import TranscriptionBackend

__all__ = ["build_backend", "default_dir", "is_available", "resolve_dir"]

_DIR_NAME = "whispercpp"
_EXE_NAMES = ("whisper-cli.exe", "whisper-cli")


def default_dir() -> Path:
    return Path.home() / ".cache" / "voicemate" / _DIR_NAME


def resolve_dir(config: Config) -> Path:
    return Path(config.whispercpp_dir) if config.whispercpp_dir else default_dir()


def find_exe(directory: Path) -> Path | None:
    for name in _EXE_NAMES:
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def find_model(directory: Path) -> Path | None:
    models = sorted(directory.glob("ggml-*.bin"))
    return models[0] if models else None


def is_available(config: Config) -> bool:
    """True se o binário whisper-cli e um modelo GGUF existem no diretório."""
    directory = resolve_dir(config)
    return find_exe(directory) is not None and find_model(directory) is not None


def build_backend(config: Config) -> TranscriptionBackend:
    directory = resolve_dir(config)
    exe = find_exe(directory)
    model = find_model(directory)
    if exe is None or model is None:
        raise FileNotFoundError(f"whisper.cpp não encontrado em {directory} (rode `make configure`).")
    from app.features.whispercpp.backend import WhisperCppBackend

    return WhisperCppBackend(config, exe, model)
