"""`app.setup.stt_modes`: the readiness report the companion reads and the install routing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.setup import stt_modes
from app.setup.gpu_detect import GpuInfo
from app.setup.persisted_config import PersistedConfig


@pytest.fixture(autouse=True)
def _machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An AMD box on WSL2 with only the CPU mode installed (her machine before the GPU install)."""
    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="amd"))
    monkeypatch.setattr(stt_modes, "detect_platform", lambda: "wsl2")
    monkeypatch.setattr(stt_modes, "detect_gpu", lambda: GpuInfo(vendor="amd"))
    monkeypatch.setattr(stt_modes, "_has_module", lambda name: name == "faster_whisper")
    monkeypatch.setattr(stt_modes, "_faster_whisper_model_cached", lambda model: True)
    monkeypatch.setattr(stt_modes, "_linux_rocm_torch_already_ok", lambda: False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))


def test_check_reports_cpu_ready_and_gpu_missing_with_download_size() -> None:
    report = stt_modes.check("large-v3-turbo", "large-v3-turbo")
    assert report["cpu"]["ready"] is True
    assert report["cpu"]["download_mb"] == 0
    gpu = report["gpu"]
    assert (gpu["ready"], gpu["supported"]) == (False, True)
    assert gpu["download_mb"] > 4000  # ROCm torch + the model
    assert "openai-whisper" in gpu["detail"]


def test_gpu_ready_when_torch_whisper_and_model_are_there(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stt_modes, "_linux_rocm_torch_already_ok", lambda: True)
    monkeypatch.setattr(stt_modes, "_has_module", lambda name: True)
    (tmp_path / "whisper").mkdir()
    (tmp_path / "whisper" / "medium.pt").write_bytes(b"")
    assert stt_modes.check("large-v3-turbo", "medium")["gpu"]["ready"] is True


def test_validated_ct2_rocm_counts_as_gpu_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="amd", ct2_rocm_ok=True))
    assert stt_modes.check("large-v3-turbo", "large-v3-turbo")["gpu"]["ready"] is True


def test_no_gpu_means_gpu_mode_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="cpu"))
    monkeypatch.setattr(stt_modes, "detect_gpu", lambda: GpuInfo(vendor="cpu"))
    gpu = stt_modes.check("large-v3-turbo", "large-v3-turbo")["gpu"]
    assert (gpu["ready"], gpu["supported"]) == (False, False)


def test_saved_cpu_vendor_still_detects_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="cpu"))
    assert stt_modes.check("large-v3-turbo", "large-v3-turbo")["gpu"]["supported"] is True


def test_cpu_mode_missing_model_asks_for_a_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "_faster_whisper_model_cached", lambda model: False)
    cpu = stt_modes.check("medium", "large-v3-turbo")["cpu"]
    assert cpu["ready"] is False
    assert cpu["download_mb"] == 1500


def test_check_json_prints_one_parseable_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert stt_modes.main(["check", "--json"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert set(json.loads(lines[0])) == {"cpu", "gpu"}


def test_install_gpu_on_amd_linux_installs_torch_whisper_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(stt_modes, "_install_torch", lambda vendor, platform: vendor == "amd")

    def run(cmd: list[str], desc: str) -> bool:
        commands.append(cmd)
        return True

    monkeypatch.setattr(stt_modes, "_run", run)
    monkeypatch.setattr(stt_modes, "_check_gpu", lambda model: stt_modes._status(True, "ok"))
    assert stt_modes.main(["install", "gpu", "--model", "medium"]) == 0
    pip_install, download = commands
    assert "openai-whisper" in pip_install
    assert stt_modes._ROCM_LINUX_NUMPY_PIN in pip_install  # never lift numpy past the ROCm build
    assert "medium" in download


def test_install_gpu_persists_the_vendor_after_a_cpu_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, object] = {}
    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="cpu"))
    monkeypatch.setattr(stt_modes, "update_persisted", lambda **changes: saved.update(changes))
    monkeypatch.setattr(stt_modes, "_install_amd_linux", lambda model: True)
    monkeypatch.setattr(stt_modes, "_check_gpu", lambda model: stt_modes._status(True, "ok"))
    assert stt_modes.install_gpu("large-v3-turbo") is True
    assert saved == {"gpu_vendor": "amd"}


def test_install_gpu_without_a_card_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="cpu"))
    monkeypatch.setattr(stt_modes, "detect_gpu", lambda: GpuInfo(vendor="cpu"))
    assert stt_modes.main(["install", "gpu"]) == 1


def test_install_failure_returns_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "_install_torch", lambda vendor, platform: False)
    assert stt_modes.main(["install", "gpu"]) == 1


def test_nvidia_needs_the_card_and_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="nvidia"))
    monkeypatch.setattr(stt_modes, "_cuda_devices", lambda: 0)
    assert stt_modes.check("large-v3-turbo", "medium")["gpu"]["download_mb"] > 3500  # CUDA libraries + model
    monkeypatch.setattr(stt_modes, "_cuda_devices", lambda: 1)
    monkeypatch.setattr(stt_modes, "_faster_whisper_model_cached", lambda model: model != "medium")
    gpu = stt_modes.check("large-v3-turbo", "medium")["gpu"]
    assert (gpu["ready"], gpu["download_mb"]) == (False, 1500)  # no silent download on the first start
    assert stt_modes.check("large-v3-turbo", "large-v3-turbo")["gpu"]["ready"] is True


def test_install_gpu_on_nvidia_installs_cuda_then_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def record(name: str, *_args: object) -> bool:
        calls.append(name)
        return True

    monkeypatch.setattr(stt_modes, "load_persisted", lambda: PersistedConfig(gpu_vendor="nvidia"))
    monkeypatch.setattr(stt_modes, "_install_torch", record)
    monkeypatch.setattr(stt_modes, "_download_faster_whisper", record)
    monkeypatch.setattr(stt_modes, "_check_gpu", lambda model: stt_modes._status(True, "ok"))
    assert stt_modes.install_gpu("small") is True
    assert calls == ["nvidia", "small"]


def test_windows_amd_uses_whispercpp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt_modes, "detect_platform", lambda: "windows")
    monkeypatch.setattr(stt_modes, "_whispercpp_ready", lambda: False)
    assert stt_modes.check("large-v3-turbo", "large-v3-turbo")["gpu"]["download_mb"] == stt_modes._WHISPERCPP_MB
    installs: list[str] = []

    def install_whispercpp() -> bool:
        installs.append("whispercpp")
        return True

    monkeypatch.setattr(stt_modes, "_install_whispercpp", install_whispercpp)
    monkeypatch.setattr(stt_modes, "_install_torch", lambda vendor, platform: pytest.fail("no torch on this path"))
    monkeypatch.setattr(stt_modes, "_check_gpu", lambda model: stt_modes._status(True, "ok"))
    assert stt_modes.install_gpu("large-v3-turbo") is True
    assert installs == ["whispercpp"]
