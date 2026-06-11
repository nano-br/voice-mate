from __future__ import annotations

from pathlib import Path

from app.core.config import Config
from app.features import whispercpp


def test_resolve_dir_default() -> None:
    cfg = Config()
    assert whispercpp.resolve_dir(cfg) == Path.home() / ".cache" / "voicemate" / "whispercpp"


def test_resolve_dir_override(tmp_path: Path) -> None:
    cfg = Config(whispercpp_dir=str(tmp_path))
    assert whispercpp.resolve_dir(cfg) == tmp_path


def test_is_available_false_when_dir_empty(tmp_path: Path) -> None:
    cfg = Config(whispercpp_dir=str(tmp_path))
    assert whispercpp.is_available(cfg) is False


def test_is_available_true_with_exe_and_model(tmp_path: Path) -> None:
    (tmp_path / "whisper-cli.exe").write_bytes(b"x")
    (tmp_path / "ggml-large-v3-turbo.bin").write_bytes(b"x")
    cfg = Config(whispercpp_dir=str(tmp_path))
    assert whispercpp.is_available(cfg) is True


def test_is_available_false_with_exe_but_no_model(tmp_path: Path) -> None:
    (tmp_path / "whisper-cli.exe").write_bytes(b"x")
    cfg = Config(whispercpp_dir=str(tmp_path))
    assert whispercpp.is_available(cfg) is False


def test_find_model_picks_ggml_bin(tmp_path: Path) -> None:
    (tmp_path / "ggml-large-v3-turbo.bin").write_bytes(b"x")
    found = whispercpp.find_model(tmp_path)
    assert found is not None
    assert found.name == "ggml-large-v3-turbo.bin"


def test_find_model_ignores_vad_models(tmp_path: Path) -> None:
    (tmp_path / "ggml-silero-v5.1.2.bin").write_bytes(b"vad")
    (tmp_path / "ggml-large-v3-turbo.bin").write_bytes(b"model")
    model = whispercpp.find_model(tmp_path)
    assert model is not None
    assert model.name == "ggml-large-v3-turbo.bin"


def test_find_vad_model(tmp_path: Path) -> None:
    assert whispercpp.find_vad_model(tmp_path) is None
    (tmp_path / "ggml-large-v3-turbo.bin").write_bytes(b"model")
    assert whispercpp.find_vad_model(tmp_path) is None
    (tmp_path / "ggml-silero-v5.1.2.bin").write_bytes(b"vad")
    vad = whispercpp.find_vad_model(tmp_path)
    assert vad is not None
    assert vad.name == "ggml-silero-v5.1.2.bin"


def test_is_available_with_linux_binaries(tmp_path: Path) -> None:
    # Linux: binários sem .exe
    (tmp_path / "whisper-server").write_bytes(b"x")
    (tmp_path / "ggml-large-v3-turbo-q8_0.bin").write_bytes(b"x")
    cfg = Config(whispercpp_dir=str(tmp_path), whispercpp_mode="server")
    assert whispercpp.is_available(cfg) is True
