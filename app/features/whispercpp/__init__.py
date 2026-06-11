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

import sys
from pathlib import Path

from app.core.config import Config
from app.core.transcription_backend import TranscriptionBackend

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
    """Modelo de transcrição (exclui o modelo de VAD, que também é ggml-*.bin)."""
    models = sorted(
        path for path in directory.glob("ggml-*.bin") if "silero" not in path.name and "vad" not in path.name
    )
    return models[0] if models else None


def find_vad_model(directory: Path) -> Path | None:
    """Modelo silero-VAD (corte por silêncio — evita cortar no meio de palavras)."""
    models = sorted(path for path in directory.glob("ggml-*.bin") if "silero" in path.name or "vad" in path.name)
    return models[0] if models else None


def is_available(config: Config) -> bool:
    """True se há um modelo GGUF e ao menos um binário (server p/ modo server, cli senão)."""
    directory = resolve_dir(config)
    if find_model(directory) is None:
        return False
    if config.whispercpp_mode == "server":
        # server cai p/ cli se o whisper-server.exe não existir, então qualquer um serve.
        return find_server_exe(directory) is not None or find_exe(directory) is not None
    return find_exe(directory) is not None


def _build_cli_backend(config: Config, directory: Path, model: Path) -> TranscriptionBackend:
    exe = find_exe(directory)
    if exe is None:
        raise FileNotFoundError(f"whisper-cli não encontrado em {directory} (rode `make configure`).")
    from app.features.whispercpp.backend import WhisperCppBackend

    return WhisperCppBackend(config, exe, model)


def build_backend(config: Config) -> TranscriptionBackend:
    directory = resolve_dir(config)
    model = find_model(directory)
    if model is None:
        raise FileNotFoundError(f"Modelo GGUF do whisper.cpp não encontrado em {directory} (rode `make configure`).")

    if config.whispercpp_mode == "server":
        server_exe = find_server_exe(directory)
        if server_exe is not None:
            from app.features.whispercpp.server_backend import WhisperCppServerBackend

            try:
                return WhisperCppServerBackend(config, server_exe, model)
            except Exception as exc:  # noqa: BLE001 — qualquer falha de subida cai p/ cli
                print(
                    f"[VoiceMate] ⚠ whisper-server falhou ao subir ({exc}); caindo para whisper-cli.",
                    file=sys.stderr,
                )
        else:
            print(
                "[VoiceMate] ⚠ whisper-server.exe não encontrado; usando whisper-cli (modo cli).",
                file=sys.stderr,
            )

    return _build_cli_backend(config, directory, model)
