"""Cadeia de seleção do backend STT em `cli.wiring.build_transcriber`."""

from __future__ import annotations

from typing import Any

import pytest

from app.cli import wiring
from app.core.config import Config


class FakeBackend:
    def __init__(self, name: str, **kwargs: Any) -> None:  # noqa: ANN401
        self.name = name
        self.kwargs = kwargs

    def transcribe(self, audio: Any) -> str:  # noqa: ANN401
        return self.name


class FakeFasterWhisper:
    """Substitui a classe FasterWhisperBackend; registra como foi construída."""

    calls: list[dict[str, Any]] = []
    raise_on_gpu: bool = False

    def __init__(self, model_size: str, use_cpu: bool, beam_size: int, language: str | None = None) -> None:
        type(self).calls.append(
            {"model_size": model_size, "use_cpu": use_cpu, "beam_size": beam_size, "language": language}
        )
        if not use_cpu and type(self).raise_on_gpu:
            raise RuntimeError("CT2 sem GPU")
        self.use_cpu = use_cpu

    def transcribe(self, audio: Any) -> str:  # noqa: ANN401
        return "faster-whisper"


@pytest.fixture(autouse=True)
def _patch_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeFasterWhisper.calls = []
    FakeFasterWhisper.raise_on_gpu = False
    monkeypatch.setattr(wiring, "FasterWhisperBackend", FakeFasterWhisper)
    # Defaults: nada de whispercpp/openai disponível — cada teste habilita o que precisa.
    monkeypatch.setattr(wiring.whispercpp_feature, "is_available", lambda config: False)
    monkeypatch.setattr(wiring.openai_whisper_feature, "is_available", lambda: False)


def _enable_whispercpp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring.whispercpp_feature, "is_available", lambda config: True)
    monkeypatch.setattr(wiring.whispercpp_feature, "build_backend", lambda config: FakeBackend("whispercpp"))


def _enable_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wiring.openai_whisper_feature, "is_available", lambda: True)
    monkeypatch.setattr(wiring.openai_whisper_feature, "build_backend", lambda config: FakeBackend("openai-whisper"))


def test_cpu_flag_forces_faster_whisper_cpu() -> None:
    backend = wiring.build_transcriber(Config(use_cpu=True, gpu_vendor="nvidia"))
    assert isinstance(backend, FakeFasterWhisper)
    assert FakeFasterWhisper.calls[-1]["use_cpu"] is True


def test_nvidia_default_uses_faster_whisper_gpu_with_language() -> None:
    backend = wiring.build_transcriber(Config(gpu_vendor="nvidia", transcription_language="pt"))
    assert isinstance(backend, FakeFasterWhisper)
    assert FakeFasterWhisper.calls[-1] == {
        "model_size": "large-v3-turbo",
        "use_cpu": False,
        "beam_size": 5,
        "language": "pt",
    }


def test_language_auto_maps_to_none() -> None:
    wiring.build_transcriber(Config(gpu_vendor="nvidia", transcription_language="auto"))
    assert FakeFasterWhisper.calls[-1]["language"] is None


def test_amd_auto_with_ct2_ok_uses_faster_whisper_gpu() -> None:
    config = Config(gpu_vendor="amd", whisper_backend="whispercpp", stt_strategy="auto", ct2_rocm_ok=True)
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeFasterWhisper)
    assert FakeFasterWhisper.calls[-1]["use_cpu"] is False


def test_amd_auto_without_ct2_ok_uses_whispercpp(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_whispercpp(monkeypatch)
    config = Config(gpu_vendor="amd", whisper_backend="whispercpp", stt_strategy="auto", ct2_rocm_ok=None)
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"
    assert FakeFasterWhisper.calls == []  # CT2-ROCm nem foi tentado


def test_amd_ct2_failure_persists_flag_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_whispercpp(monkeypatch)
    FakeFasterWhisper.raise_on_gpu = True
    persisted: list[dict[str, Any]] = []
    import app.setup.persisted_config as pc

    monkeypatch.setattr(pc, "update_persisted", lambda **kw: persisted.append(kw))

    config = Config(gpu_vendor="amd", whisper_backend="whispercpp", stt_strategy="auto", ct2_rocm_ok=True)
    backend = wiring.build_transcriber(config)

    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"
    assert persisted == [{"ct2_rocm_ok": False}]


def test_amd_explicit_openai_whisper_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_whispercpp(monkeypatch)
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", whisper_backend="openai-whisper", stt_strategy="auto", ct2_rocm_ok=True)
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "openai-whisper"


def test_amd_strategy_whispercpp_skips_ct2(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_whispercpp(monkeypatch)
    config = Config(gpu_vendor="amd", stt_strategy="whispercpp", ct2_rocm_ok=True)
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"
    assert FakeFasterWhisper.calls == []


def test_amd_wsl2_prefers_openai_over_whispercpp(monkeypatch: pytest.MonkeyPatch) -> None:
    # No WSL2 o Vulkan do whisper.cpp é llvmpipe (CPU) — openai-whisper (torch
    # ROCm) deve vir ANTES, mesmo com os dois disponíveis.
    _enable_whispercpp(monkeypatch)
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", platform="wsl2", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "openai-whisper"


def test_amd_wsl2_falls_back_to_whispercpp_when_openai_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_whispercpp(monkeypatch)  # openai indisponível (default da fixture)
    config = Config(gpu_vendor="amd", platform="wsl2", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"


def test_amd_linux_native_prefers_whispercpp(monkeypatch: pytest.MonkeyPatch) -> None:
    # Linux nativo: RADV alcança a GPU — whisper.cpp+Vulkan segue na frente.
    _enable_whispercpp(monkeypatch)
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", platform="linux-x11", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"


def test_amd_chain_falls_back_to_openai_then_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    # whispercpp indisponível, openai disponível → openai
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "openai-whisper"


def test_amd_nothing_available_falls_back_to_cpu() -> None:
    config = Config(gpu_vendor="amd", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeFasterWhisper)
    assert FakeFasterWhisper.calls[-1]["use_cpu"] is True
