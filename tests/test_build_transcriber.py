"""STT backend selection chain in `cli.wiring.build_transcriber`."""

from __future__ import annotations

from typing import Any

import pytest

from app.cli import wiring
from app.core.config import Config
from app.protocol.models import SttMode


class FakeBackend:
    def __init__(self, name: str, **kwargs: Any) -> None:  # noqa: ANN401
        self.name = name
        self.kwargs = kwargs

    def transcribe(self, audio: Any) -> str:  # noqa: ANN401
        return self.name


class FakeFasterWhisper:
    """Replaces the FasterWhisperBackend class; records how it was constructed."""

    calls: list[dict[str, Any]] = []
    raise_on_gpu: bool = False

    def __init__(
        self,
        model_size: str,
        use_cpu: bool,
        beam_size: int,
        language: str | None = None,
        cpu_threads: int = 0,
        vad_filter: bool = False,
    ) -> None:
        type(self).calls.append(
            {
                "model_size": model_size,
                "use_cpu": use_cpu,
                "beam_size": beam_size,
                "language": language,
                "cpu_threads": cpu_threads,
                "vad_filter": vad_filter,
            }
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
    # Defaults: no whispercpp/openai available — each test enables what it needs.
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
        "cpu_threads": 0,
        "vad_filter": False,
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
    # Native Linux: on WSL2 whisper.cpp is left out of the automatic chain.
    config = Config(
        gpu_vendor="amd", platform="linux-x11", whisper_backend="whispercpp", stt_strategy="auto", ct2_rocm_ok=None
    )
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"
    assert FakeFasterWhisper.calls == []  # CT2-ROCm was never even attempted


def test_amd_ct2_failure_persists_flag_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_whispercpp(monkeypatch)
    FakeFasterWhisper.raise_on_gpu = True
    persisted: list[dict[str, Any]] = []
    import app.setup.persisted_config as pc

    monkeypatch.setattr(pc, "update_persisted", lambda **kw: persisted.append(kw))

    config = Config(
        gpu_vendor="amd", platform="linux-x11", whisper_backend="whispercpp", stt_strategy="auto", ct2_rocm_ok=True
    )
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
    # On WSL2 whisper.cpp's Vulkan is llvmpipe (CPU) — openai-whisper (torch
    # ROCm) must come FIRST, even when both are available.
    _enable_whispercpp(monkeypatch)
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", platform="wsl2", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "openai-whisper"


def test_amd_wsl2_skips_whispercpp_and_uses_cpu_when_openai_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # On WSL2 whisper.cpp only reaches llvmpipe (the CPU) and is slower there than
    # faster-whisper int8, so the chain goes straight to faster-whisper on the CPU.
    _enable_whispercpp(monkeypatch)  # openai unavailable (fixture default)
    config = Config(gpu_vendor="amd", platform="wsl2", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeFasterWhisper)
    assert FakeFasterWhisper.calls[-1]["use_cpu"] is True


def test_amd_wsl2_explicit_whispercpp_strategy_still_uses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_whispercpp(monkeypatch)
    config = Config(gpu_vendor="amd", platform="wsl2", stt_strategy="whispercpp")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"


def test_amd_linux_native_prefers_whispercpp(monkeypatch: pytest.MonkeyPatch) -> None:
    # Native Linux: RADV reaches the GPU — whisper.cpp+Vulkan stays ahead.
    _enable_whispercpp(monkeypatch)
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", platform="linux-x11", stt_strategy="auto")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "whispercpp"


def test_amd_chain_falls_back_to_openai_then_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    # whispercpp unavailable, openai available → openai
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


def test_cpu_mode_uses_greedy_vad_and_threads_even_with_a_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", platform="wsl2", stt_mode="cpu", cpu_threads=4, beam_size=5)
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeFasterWhisper)
    call = FakeFasterWhisper.calls[-1]
    assert (call["use_cpu"], call["beam_size"], call["vad_filter"], call["cpu_threads"]) == (True, 1, True, 4)


def test_gpu_mode_uses_the_gpu_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_openai(monkeypatch)
    config = Config(gpu_vendor="amd", platform="wsl2", stt_mode="gpu")
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeBackend)
    assert backend.name == "openai-whisper"


def test_gpu_mode_decodes_greedily_and_auto_keeps_the_configured_beam(monkeypatch: pytest.MonkeyPatch) -> None:
    beams: list[int] = []

    def build(config: Config) -> FakeBackend:
        beams.append(config.beam_size)
        return FakeBackend("openai-whisper")

    monkeypatch.setattr(wiring.openai_whisper_feature, "is_available", lambda: True)
    monkeypatch.setattr(wiring.openai_whisper_feature, "build_backend", build)
    modes: tuple[SttMode, ...] = ("gpu", "auto")
    for mode in modes:
        wiring.build_transcriber(Config(gpu_vendor="amd", platform="wsl2", stt_mode=mode, beam_size=5))
    assert beams == [1, 5]


@pytest.mark.parametrize("mode", ["gpu", "auto"])
def test_gpu_mode_without_gpu_backend_falls_back_to_cpu_with_hint(
    capsys: pytest.CaptureFixture[str], mode: SttMode
) -> None:
    config = Config(gpu_vendor="amd", platform="wsl2", stt_mode=mode)
    backend = wiring.build_transcriber(config)
    assert isinstance(backend, FakeFasterWhisper)
    assert FakeFasterWhisper.calls[-1]["use_cpu"] is True
    assert "stt-install MODE=gpu" in capsys.readouterr().err


def test_gpu_mode_without_a_gpu_vendor_falls_back_to_cpu_with_hint(capsys: pytest.CaptureFixture[str]) -> None:
    backend = wiring.build_transcriber(Config(gpu_vendor="cpu", stt_mode="gpu"))
    assert isinstance(backend, FakeFasterWhisper)
    assert FakeFasterWhisper.calls[-1]["use_cpu"] is True
    assert "stt-install MODE=gpu" in capsys.readouterr().err


def test_describe_transcriber_reports_the_effective_engine() -> None:
    class Built:
        name = "openai-whisper"
        device = "gpu"
        model_name = "medium"

        def transcribe(self, audio: Any) -> str:  # noqa: ANN401
            return ""

    info = wiring.describe_transcriber(Built(), Config(stt_mode="gpu", model_size="medium"))
    assert info == {"mode": "gpu", "device": "gpu", "backend": "openai-whisper", "model": "medium"}


def test_describe_transcriber_without_attributes_derives_from_config() -> None:
    info = wiring.describe_transcriber(FakeBackend("x"), Config(use_cpu=True))
    assert info["device"] == "cpu"
    assert info["mode"] == "auto"
