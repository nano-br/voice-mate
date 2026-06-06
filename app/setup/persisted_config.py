"""Read/write the user's remembered choices at ``~/.config/voicemate/config.toml``.

Read uses stdlib ``tomllib`` (Python 3.11+). Write uses a tiny hand-rolled
flat writer (the schema is a controlled, flat key set — no need for a TOML
serializer dependency). Every operation degrades gracefully: a missing or
corrupt file yields an all-``None`` config (the app then falls back to its
built-in safe defaults).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import FlowKind, GpuVendor, WhisperBackend

CONFIG_DIR = Path.home() / ".config" / "voicemate"
CONFIG_PATH = CONFIG_DIR / "config.toml"

_VENDORS: tuple[GpuVendor, ...] = ("nvidia", "amd", "cpu")
_BACKENDS: tuple[WhisperBackend, ...] = ("faster-whisper", "openai-whisper")
_FLOWS: tuple[FlowKind, ...] = ("clipboard", "claude_chat")


@dataclass
class PersistedConfig:
    """Escolhas lembradas entre execuções. Tudo opcional (None = não definido)."""

    gpu_vendor: GpuVendor | None = None
    whisper_backend: WhisperBackend | None = None
    tts_enabled: bool | None = None
    default_flow: FlowKind | None = None

    def is_empty(self) -> bool:
        return (
            self.gpu_vendor is None
            and self.whisper_backend is None
            and self.tts_enabled is None
            and self.default_flow is None
        )


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
    )


def save_persisted(cfg: PersistedConfig, path: Path = CONFIG_PATH) -> None:
    """Grava de forma atômica. Campos None são omitidos."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# VoiceMate — escolhas lembradas.",
        "# Geradas por `make setup` / `make configure`. Edite ou apague à vontade.",
        "",
    ]
    fields: dict[str, str | bool | None] = {
        "gpu_vendor": cfg.gpu_vendor,
        "whisper_backend": cfg.whisper_backend,
        "tts_enabled": cfg.tts_enabled,
        "default_flow": cfg.default_flow,
    }
    for key, value in fields.items():
        if value is None:
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    content = "\n".join(lines) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)  # atômico no mesmo filesystem (sobrescreve se existir)


def _read_choice(data: dict[str, Any], key: str, allowed: tuple[str, ...]) -> Any:  # noqa: ANN401
    value = data.get(key)
    if isinstance(value, str) and value in allowed:
        return value
    return None


def _read_bool(data: dict[str, Any], key: str) -> bool | None:
    value = data.get(key)
    return value if isinstance(value, bool) else None


def _toml_value(value: str | bool) -> str:
    if isinstance(value, bool):  # bool antes de str (não há ints no schema)
        return "true" if value else "false"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
