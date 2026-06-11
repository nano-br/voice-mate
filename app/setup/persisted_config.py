"""Read/write the user's remembered choices at ``~/.config/voicemate/config.toml``.

Read uses stdlib ``tomllib`` (Python 3.11+). Write uses a tiny hand-rolled
flat writer (the schema is a controlled, flat key set — no need for a TOML
serializer dependency). Every operation degrades gracefully: a missing or
corrupt file yields an all-``None`` config (the app then falls back to its
built-in safe defaults).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from app.core.config import FlowKind, GpuVendor, SttStrategy, WhisperBackend
from app.platform.kinds import PlatformKind, TriggerKind

CONFIG_DIR = Path.home() / ".config" / "voicemate"
CONFIG_PATH = CONFIG_DIR / "config.toml"

_VENDORS: tuple[GpuVendor, ...] = ("nvidia", "amd", "cpu")
_BACKENDS: tuple[WhisperBackend, ...] = ("faster-whisper", "whispercpp", "openai-whisper")
_FLOWS: tuple[FlowKind, ...] = ("clipboard", "claude_chat")
_PLATFORMS: tuple[PlatformKind, ...] = ("windows", "linux-x11", "linux-wayland", "wsl2")
_TRIGGERS: tuple[TriggerKind, ...] = ("keyboard-hooks", "pynput", "evdev", "socket")
_STT_STRATEGIES: tuple[SttStrategy, ...] = ("auto", "faster-whisper-rocm", "whispercpp", "openai-whisper")


@dataclass
class PersistedConfig:
    """Escolhas lembradas entre execuções. Tudo opcional (None = não definido)."""

    gpu_vendor: GpuVendor | None = None
    whisper_backend: WhisperBackend | None = None
    tts_enabled: bool | None = None
    default_flow: FlowKind | None = None
    platform: PlatformKind | None = None
    trigger: TriggerKind | None = None
    stt_strategy: SttStrategy | None = None
    # Resultado da validação do build CTranslate2-ROCm (setup): True = ok,
    # False = falhou (não re-tentar a cada boot), None = nunca tentado.
    ct2_rocm_ok: bool | None = None
    daemon_port: int | None = None

    def is_empty(self) -> bool:
        return all(getattr(self, f.name) is None for f in fields(self))


def load_persisted(path: Path = CONFIG_PATH) -> PersistedConfig:
    """Carrega o config persistido. Arquivo ausente/corrompido → tudo None."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return PersistedConfig()
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return PersistedConfig()
    return PersistedConfig(
        gpu_vendor=_read_choice(data, "gpu_vendor", _VENDORS),
        whisper_backend=_read_choice(data, "whisper_backend", _BACKENDS),
        tts_enabled=_read_bool(data, "tts_enabled"),
        default_flow=_read_choice(data, "default_flow", _FLOWS),
        platform=_read_choice(data, "platform", _PLATFORMS),
        trigger=_read_choice(data, "trigger", _TRIGGERS),
        stt_strategy=_read_choice(data, "stt_strategy", _STT_STRATEGIES),
        ct2_rocm_ok=_read_bool(data, "ct2_rocm_ok"),
        daemon_port=_read_int(data, "daemon_port"),
    )


def save_persisted(cfg: PersistedConfig, path: Path = CONFIG_PATH) -> None:
    """Grava de forma atômica. Campos None são omitidos."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# VoiceMate — escolhas lembradas.",
        "# Geradas por `make setup` / `make configure`. Edite ou apague à vontade.",
        "",
    ]
    values: dict[str, str | bool | int | None] = {f.name: getattr(cfg, f.name) for f in fields(PersistedConfig)}
    for key, value in values.items():
        if value is None:
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    content = "\n".join(lines) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)  # atômico no mesmo filesystem (sobrescreve se existir)


def update_persisted(path: Path = CONFIG_PATH, **changes: str | bool | int | None) -> None:
    """Merge pontual: carrega, aplica os campos passados e regrava."""
    cfg = load_persisted(path)
    for key, value in changes.items():
        setattr(cfg, key, value)
    save_persisted(cfg, path)


def _read_choice(data: dict[str, Any], key: str, allowed: tuple[str, ...]) -> Any:  # noqa: ANN401
    value = data.get(key)
    if isinstance(value, str) and value in allowed:
        return value
    return None


def _read_bool(data: dict[str, Any], key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _read_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    # bool é subclasse de int em Python — excluir explicitamente.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _toml_value(value: str | bool | int) -> str:
    if isinstance(value, bool):  # bool antes de int (bool é subclasse de int)
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
