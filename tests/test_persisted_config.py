from __future__ import annotations

from pathlib import Path

from app.setup.persisted_config import PersistedConfig, load_persisted, save_persisted


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    cfg = PersistedConfig(
        gpu_vendor="amd",
        whisper_backend="openai-whisper",
        tts_enabled=False,
        default_flow="clipboard",
    )
    save_persisted(cfg, path)
    assert load_persisted(path) == cfg


def test_missing_file_is_empty(tmp_path: Path) -> None:
    loaded = load_persisted(tmp_path / "does-not-exist.toml")
    assert loaded.is_empty()


def test_corrupt_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("this is not = valid = toml ===", encoding="utf-8")
    assert load_persisted(path).is_empty()


def test_invalid_enum_values_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'gpu_vendor = "intel"\nwhisper_backend = "whisper.cpp"\ndefault_flow = "telepathy"\n',
        encoding="utf-8",
    )
    loaded = load_persisted(path)
    assert loaded.gpu_vendor is None
    assert loaded.whisper_backend is None
    assert loaded.default_flow is None


def test_save_omits_none_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_persisted(PersistedConfig(gpu_vendor="cpu"), path)
    text = path.read_text(encoding="utf-8")
    assert 'gpu_vendor = "cpu"' in text
    assert "whisper_backend" not in text
    assert "tts_enabled" not in text
    assert "default_flow" not in text


def test_save_writes_bool_as_toml_lowercase(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    save_persisted(PersistedConfig(tts_enabled=True), path)
    assert "tts_enabled = true" in path.read_text(encoding="utf-8")
