"""Check and install the speech-to-text modes (CPU and GPU) without the full setup.

Used by the companion app (through WSL on Windows) to offer "switch to GPU mode"
and install what is missing with the user's consent, and by `make stt-check` /
`make stt-install`:

    poetry run python -m app.setup.stt_modes check [--json] [--cpu-model M] [--gpu-model M]
    poetry run python -m app.setup.stt_modes install {cpu,gpu} [--model M]

- cpu: faster-whisper int8 (a core dependency). Installing only pre-downloads the
  model, so the first start does not stall on a silent 1.6 GB download.
- gpu: depends on the card. AMD on Linux/WSL2 uses openai-whisper on ROCm torch
  (AMD's wheels, installed into THIS environment only: the poetry venv); AMD on
  Windows uses whisper.cpp + Vulkan; NVIDIA uses faster-whisper on CUDA.

Like `gpu_bootstrap`, it never imports torch in-process (it may be missing or
broken): torch is probed in a subprocess. `check --json` prints exactly one JSON
line (an `app.protocol.models.SttCheckReport`) on stdout; the human messages go
to stderr so the JSON stays parseable.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import cast

from app.core.config import GpuVendor
from app.core.console import force_utf8_stdio
from app.i18n import _
from app.platform.detect import detect_platform
from app.platform.kinds import PlatformKind
from app.protocol.models import DEFAULT_STT_MODEL, STT_MODELS, SttCheckReport, SttModel, SttModeStatus
from app.setup.gpu_bootstrap import (
    _ROCM_LINUX_NUMPY_PIN,
    _install_torch,
    _install_whispercpp,
    _linux_rocm_torch_already_ok,
    _run,
)
from app.setup.gpu_detect import detect_gpu
from app.setup.persisted_config import load_persisted, update_persisted

# Rough download sizes in MB, only for the consent question ("about 1.6 GB").
_FASTER_WHISPER_MB: dict[SttModel, int] = {"large-v3-turbo": 1600, "large-v3": 3100, "medium": 1500, "small": 480}
_OPENAI_WHISPER_MB: dict[SttModel, int] = {"large-v3-turbo": 1550, "large-v3": 2950, "medium": 1450, "small": 460}
_ROCM_TORCH_MB = 4300  # torch + torchvision + torchaudio + triton (AMD's manylinux wheels)
_CUDA_TORCH_MB = 3500
_WHISPERCPP_MB = 1700  # binaries + the turbo GGUF model

# openai-whisper's own extra dependencies, installed with pip (not `poetry install
# --extras whisper-gpu`): poetry would resolve torch from PyPI and replace the ROCm
# build with a CUDA one.
_OPENAI_WHISPER_PACKAGES = ["openai-whisper", "silero-vad"]


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = _parse_args(argv)
    if args.command == "check":
        report = check(cast(SttModel, args.cpu_model), cast(SttModel, args.gpu_model))
        if args.json:
            print(json.dumps(report))
        else:
            _print_report(report)
        return 0
    model = cast(SttModel, args.model)
    ok = install_cpu(model) if args.mode == "cpu" else install_gpu(model)
    return 0 if ok else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="stt_modes", description=_("Check or install the speech-to-text modes."))
    sub = parser.add_subparsers(dest="command", required=True)
    check_cmd = sub.add_parser("check", help=_("Report which modes are installed."))
    check_cmd.add_argument("--json", action="store_true", help=_("Print one JSON line (for the companion app)."))
    check_cmd.add_argument("--cpu-model", choices=STT_MODELS, default=DEFAULT_STT_MODEL)
    check_cmd.add_argument("--gpu-model", choices=STT_MODELS, default=DEFAULT_STT_MODEL)
    install_cmd = sub.add_parser("install", help=_("Install what a mode needs."))
    install_cmd.add_argument("mode", choices=["cpu", "gpu"])
    install_cmd.add_argument("--model", choices=STT_MODELS, default=DEFAULT_STT_MODEL)
    return parser.parse_args(argv)


# ── check ─────────────────────────────────────────────────────────────────────


def check(cpu_model: SttModel, gpu_model: SttModel) -> SttCheckReport:
    return {"cpu": _check_cpu(cpu_model), "gpu": _check_gpu(gpu_model)}


def _status(ready: bool, detail: str, download_mb: int = 0, supported: bool = True) -> SttModeStatus:
    return {"ready": ready, "supported": supported, "detail": detail, "download_mb": 0 if ready else download_mb}


def _check_cpu(model: SttModel) -> SttModeStatus:
    if not _has_module("faster_whisper"):
        return _status(False, "faster-whisper is not installed (run `make setup`)", _FASTER_WHISPER_MB[model])
    if not _faster_whisper_model_cached(model):
        return _status(False, f"the {model} model is not downloaded yet", _FASTER_WHISPER_MB[model])
    return _status(True, f"faster-whisper {model} int8 on the CPU")


def _check_gpu(model: SttModel) -> SttModeStatus:
    vendor = _gpu_vendor()
    platform = detect_platform()
    if vendor == "cpu":
        return _status(False, "no supported GPU was found", supported=False)
    if vendor == "nvidia":
        return _check_nvidia(model)
    if platform == "windows":
        # whisper.cpp uses the GGUF model of its own folder: the model choice does not apply.
        if _whispercpp_ready():
            return _status(True, "whisper.cpp on Vulkan (its own model)")
        return _status(False, "whisper.cpp is not installed", _WHISPERCPP_MB)
    return _check_amd_linux(model)


def _check_nvidia(model: SttModel) -> SttModeStatus:
    if _cuda_devices() == 0:
        return _status(
            False,
            "CTranslate2 does not see the NVIDIA GPU (CUDA libraries missing)",
            _CUDA_TORCH_MB + _FASTER_WHISPER_MB[model],
        )
    # Same model files as the CPU mode: without them the first start would download in silence.
    if not _faster_whisper_model_cached(model):
        return _status(False, f"the {model} model is not downloaded yet", _FASTER_WHISPER_MB[model])
    return _status(True, f"faster-whisper {model} on CUDA")


def _check_amd_linux(model: SttModel) -> SttModeStatus:
    if load_persisted().ct2_rocm_ok is True:
        return _status(True, "faster-whisper on ROCm (CTranslate2 fork)")
    missing_mb = 0
    problems: list[str] = []
    if not _linux_rocm_torch_already_ok():
        problems.append("ROCm torch is missing or does not see the GPU")
        missing_mb += _ROCM_TORCH_MB
    if not _has_module("whisper"):
        problems.append("openai-whisper is not installed")
    if not _openai_whisper_model_cached(model):
        problems.append(f"the {model} model is not downloaded yet")
        missing_mb += _OPENAI_WHISPER_MB[model]
    if problems:
        return _status(False, "; ".join(problems), missing_mb)
    return _status(True, f"openai-whisper {model} on ROCm")


def _gpu_vendor() -> GpuVendor:
    saved = load_persisted().gpu_vendor
    if saved in ("nvidia", "amd"):
        return saved
    # Saved "cpu" (a setup that ran in CPU mode) or nothing: look at the hardware.
    return detect_gpu().vendor


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _faster_whisper_model_cached(model: SttModel) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(model, local_files_only=True)
    except Exception:  # noqa: BLE001 (any failure means "not usable offline")
        return False
    return True


def _openai_whisper_cache_dir() -> Path:
    # Same rule as whisper.load_model's default download_root.
    return Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "whisper"


def _openai_whisper_model_cached(model: SttModel) -> bool:
    return (_openai_whisper_cache_dir() / f"{model}.pt").is_file()


def _cuda_devices() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001
        return 0


def _whispercpp_ready() -> bool:
    from app.features import whispercpp

    directory = whispercpp.default_dir()
    return whispercpp.find_model(directory) is not None and (
        whispercpp.find_server_exe(directory) is not None or whispercpp.find_exe(directory) is not None
    )


def _print_report(report: SttCheckReport) -> None:
    for mode in ("cpu", "gpu"):
        status = report["cpu"] if mode == "cpu" else report["gpu"]
        mark = "✓" if status["ready"] else "✗"
        print(f"[stt] {mode.upper()}: {mark} {status['detail']}")
        if not status["ready"] and status["supported"]:
            print(_("[stt]   Install with: make stt-install MODE={mode}").format(mode=mode))


# ── install ───────────────────────────────────────────────────────────────────


def install_cpu(model: SttModel) -> bool:
    if not _has_module("faster_whisper") and not _run(["poetry", "install"], _("Installing dependencies (poetry)...")):
        return False
    print(_("\n[stt] Downloading the {model} model for the CPU mode...").format(model=model))
    return _download_faster_whisper(model) and _report_ready("cpu", _check_cpu(model))


def _download_faster_whisper(model: SttModel) -> bool:
    try:
        from faster_whisper.utils import download_model

        download_model(model)
    except Exception as exc:  # noqa: BLE001
        print(_("[stt] ⚠ Model download failed: {exc}").format(exc=exc), file=sys.stderr)
        return False
    return True


def install_gpu(model: SttModel) -> bool:
    vendor = _gpu_vendor()
    platform: PlatformKind = detect_platform()
    if vendor == "cpu":
        print(_("[stt] ⚠ No supported GPU was found; the GPU mode cannot be installed."), file=sys.stderr)
        return False
    if vendor == "nvidia":
        ok = _install_torch("nvidia", platform)
        if ok:
            print(_("\n[stt] Downloading the {model} model for the GPU mode...").format(model=model))
            ok = _download_faster_whisper(model)
    elif platform == "windows":
        ok = _install_whispercpp()
    else:
        ok = _install_amd_linux(model)
    if ok and load_persisted().gpu_vendor != vendor:
        # A setup made in CPU mode remembered vendor="cpu"; the GPU mode needs the card.
        update_persisted(gpu_vendor=vendor)
    return ok and _report_ready("gpu", _check_gpu(model))


def _install_amd_linux(model: SttModel) -> bool:
    # Reuses the setup's ROCm install: it keeps a working ROCm torch as is and
    # otherwise installs AMD's wheels (pip reuses its cache when they were fetched before).
    if not _install_torch("amd", detect_platform()):
        return False
    pip = [sys.executable, "-m", "pip", "install"]
    # The numpy pin goes on the same command: openai-whisper's numba must not lift
    # numpy past what the ROCm wheels were built against.
    if not _run(pip + [_ROCM_LINUX_NUMPY_PIN, *_OPENAI_WHISPER_PACKAGES], _("Installing openai-whisper...")):
        return False
    print(_("\n[stt] Downloading the {model} model for the GPU mode...").format(model=model))
    # openai-whisper has no public download call; load_model would also load it on the GPU.
    code = "import sys, whisper; whisper._download(whisper._MODELS[sys.argv[1]], sys.argv[2], False)"
    return _run([sys.executable, "-c", code, model, str(_openai_whisper_cache_dir())], _("Downloading the model..."))


def _report_ready(mode: str, status: SttModeStatus) -> bool:
    if status["ready"]:
        print(_("[stt] ✓ {mode} mode ready: {detail}").format(mode=mode.upper(), detail=status["detail"]))
        return True
    print(
        _("[stt] ⚠ {mode} mode still not ready: {detail}").format(mode=mode.upper(), detail=status["detail"]),
        file=sys.stderr,
    )
    return False


if __name__ == "__main__":
    raise SystemExit(main())
