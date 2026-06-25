"""Interactive GPU bootstrap — detect the GPU, confirm with the user, install
the matching torch + feature extras, and remember the choice.

Invoked by ``make setup`` (first run) and ``make configure`` (re-pick):

    poetry run python -m app.setup.gpu_bootstrap [--reconfigure]
                                                 [--vendor {nvidia,amd,cpu}]
                                                 [--yes] [--extras "..."]

Only stdlib + the light `app.setup` modules are imported here — never
`torch`/`voxcpm` (they may not be installed yet when this runs). User-facing
messages go through gettext, like the rest of the operational code
(wiring/config_builder).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import cast

from app.core.config import FlowKind, GpuVendor, TTSEngine, WhisperBackend
from app.core.console import force_utf8_stdio
from app.i18n import _
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind
from app.setup.gpu_detect import GpuInfo, amd_driver_warning, detect_gpu
from app.setup.persisted_config import PersistedConfig, load_persisted, save_persisted

# ── ROCm releases (one constant per track; the tracks have THEIR OWN versions) ──
# Windows (ROCm-on-Windows): wheels at https://repo.radeon.com/rocm/windows/.
_ROCM_WIN_VER = "7.2.1"
# Linux/WSL2: the LINE is "7.2" (WSL package 7.2.70200) and the manylinux wheels
# are "+rocm7.2.0" — at https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2/.
# (Not to be confused with the "7.2.4" track, which is native Linux and has no wsl usecase.)
_ROCM_LINUX_LINE = "7.2"

# Official AMD ROCm-on-Windows wheels (not on PyPI). Check/update to the latest
# release at https://repo.radeon.com/rocm/windows/ when maintaining.
_ROCM_REL = f"rocm-rel-{_ROCM_WIN_VER}"
_ROCM_BASE = f"https://repo.radeon.com/rocm/windows/{_ROCM_REL}"
# Step 1: ROCm SDK runtime. ROCm torch DEPENDS on it just to import — without
# it, `import torch` breaks with ModuleNotFoundError: rocm_sdk. (Official AMD
# command; torchvision is omitted on purpose — the project doesn't use it.)
_ROCM_SDK_WHEELS = [
    f"{_ROCM_BASE}/rocm_sdk_core-{_ROCM_WIN_VER}-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm_sdk_devel-{_ROCM_WIN_VER}-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm_sdk_libraries_custom-{_ROCM_WIN_VER}-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm-{_ROCM_WIN_VER}.tar.gz",
]
# Step 2: torch + torchaudio ROCm.
_ROCM_WHEELS = [
    f"{_ROCM_BASE}/torch-2.9.1+rocm{_ROCM_WIN_VER}-cp312-cp312-win_amd64.whl",
    f"{_ROCM_BASE}/torchaudio-2.9.1+rocm{_ROCM_WIN_VER}-cp312-cp312-win_amd64.whl",
]
_TORCH_INDEX: dict[str, str] = {
    "nvidia": "https://download.pytorch.org/whl/cu128",
    "cpu": "https://download.pytorch.org/whl/cpu",
}
# Linux/WSL2 + AMD: manylinux wheels that AMD publishes and TESTS for WSL
# (torch+torchvision+torchaudio+triton matched to the line's ROCm). Preferred
# over the pytorch.org index because they are the combination validated by AMD —
# and triton (TunableOp/flash-attn) only ships here. They require numpy < 2.0 and cp312.
_ROCM_LINUX_BASE = f"https://repo.radeon.com/rocm/manylinux/rocm-rel-{_ROCM_LINUX_LINE}"
_ROCM_LINUX_NUMPY_PIN = "numpy==1.26.4"
_ROCM_LINUX_WHEELS = [
    f"{_ROCM_LINUX_BASE}/torch-2.9.1%2Brocm7.2.0.lw.git7e1940d4-cp312-cp312-linux_x86_64.whl",
    f"{_ROCM_LINUX_BASE}/torchvision-0.24.0%2Brocm7.2.0.gitb919bd0c-cp312-cp312-linux_x86_64.whl",
    f"{_ROCM_LINUX_BASE}/torchaudio-2.9.0%2Brocm7.2.0.gite3c6ee2b-cp312-cp312-linux_x86_64.whl",
    f"{_ROCM_LINUX_BASE}/triton-3.5.1%2Brocm7.2.0.gita272dfa8-cp312-cp312-linux_x86_64.whl",
]

# whisper.cpp + Vulkan (preferred transcription backend on AMD). Native binary
# (non-pip) + GGUF model, downloaded with a pinned SHA-256 for integrity. The
# default model is turbo fp16 (reference quality, ~1.6 GB of VRAM).
_WHISPERCPP_DIR = Path.home() / ".cache" / "voicemate" / "whispercpp"
_WCPP_BIN_URL = (
    "https://github.com/jerryshell/whisper.cpp-windows-vulkan-bin/"
    "releases/download/v1.0.0/whisper.cpp-windows-vulkan.zip"
)
_WCPP_BIN_SHA256 = "a5d408c72e460433b39875f74a0b6e27e60a3724301d478fe9873db7ff4098e0"
_WCPP_MODEL_NAME = "ggml-large-v3-turbo.bin"
_WCPP_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{_WCPP_MODEL_NAME}?download=true"
_WCPP_MODEL_SHA256 = "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"
# From the zip we keep only what's needed: the CLIs + the DLLs (Vulkan/ggml/whisper).
_WCPP_KEEP_EXACT = ("whisper-cli.exe", "whisper-server.exe")

# Linux: build from source (no official Linux+Vulkan binary is distributed).
# Pinned tag for reproducibility; static binaries (BUILD_SHARED_LIBS=OFF).
_WCPP_LINUX_REPO = "https://github.com/ggml-org/whisper.cpp"
_WCPP_LINUX_TAG = "v1.8.6"
# Note: spirv-headers/spirv-tools/glslang-tools are required to compile the
# Vulkan shaders (without them cmake fails on SPIRV-Headers/glslangValidator).
# `libglslang-dev` does NOT exist under that name on Ubuntu 24.04 — don't suggest it.
_WCPP_LINUX_APT_HINT = (
    "sudo apt install -y git cmake build-essential libvulkan-dev glslc vulkan-tools "
    "spirv-headers spirv-tools glslang-tools"
)

# Q8_0 model (near-lossless, ~0.9 GB vs ~1.6 GB for fp16) — option for tight VRAM.
_WCPP_MODEL_Q8_NAME = "ggml-large-v3-turbo-q8_0.bin"
_WCPP_MODEL_Q8_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{_WCPP_MODEL_Q8_NAME}?download=true"
_WCPP_MODEL_Q8_SHA256 = "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1"

# silero-VAD in GGML (silence-based trimming — avoids cutting words mid-way).
# Only downloaded on Linux: the pinned Windows binary is old and lacks --vad.
_WCPP_VAD_NAME = "ggml-silero-v5.1.2.bin"
_WCPP_VAD_URL = f"https://huggingface.co/ggml-org/whisper-vad/resolve/main/{_WCPP_VAD_NAME}?download=true"
_WCPP_VAD_SHA256 = "29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf"


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = _parse_args(argv)
    interactive = not args.yes

    saved = load_persisted()
    platform = detect_platform()
    trigger = default_trigger(platform)
    print(_("[setup] Platform: {platform} (default trigger: {trigger})").format(platform=platform, trigger=trigger))
    info = detect_gpu()
    name = f" — {info.device_name}" if info.device_name else ""
    gfx = f" [{info.gfx_target}]" if info.gfx_target else ""
    print(_("[setup] GPU detected: {vendor}{name}{gfx}").format(vendor=info.vendor.upper(), name=name, gfx=gfx))
    warning = amd_driver_warning(info)
    if warning:
        print(warning, file=sys.stderr)

    if args.vendor is not None:
        vendor: GpuVendor = cast(GpuVendor, args.vendor)
    elif not interactive and saved.gpu_vendor is not None:
        vendor = saved.gpu_vendor
    else:
        vendor = _prompt_vendor(saved.gpu_vendor or info.vendor, interactive)

    flow = _prompt_flow(interactive, saved.default_flow or "claude_chat")
    tts_engine: TTSEngine = saved.tts_engine or "kokoro"
    if flow == "claude_chat":
        default_tts = saved.tts_enabled if saved.tts_enabled is not None else True
        tts_enabled = _prompt_yes_no(_("Enable TTS (spoken Claude response)?"), default_tts, interactive)
        if tts_enabled:
            tts_engine = _prompt_tts_engine(saved.tts_engine or "kokoro", interactive)
    else:
        tts_enabled = False

    backend: WhisperBackend = "whispercpp" if vendor == "amd" else "faster-whisper"
    extras = (
        set(args.extras.split())
        if args.extras is not None
        else _compute_extras(flow, tts_enabled, vendor, platform, tts_engine)
    )

    # Native Linux: hotkeys need pynput/evdev (linux extra). WSL2 uses the
    # HTTP daemon (no extra dependency).
    if platform in ("linux-x11", "linux-wayland"):
        extras.add("linux")

    print(
        _(
            "\n[setup] Plan: platform={platform}, vendor={vendor}, transcription={backend}, "
            "flow={flow}, tts={tts}, extras=[{extras}]"
        ).format(
            platform=platform,
            vendor=vendor,
            backend=backend,
            flow=flow,
            tts=tts_enabled,
            extras=" ".join(sorted(extras)) or "—",
        )
    )

    _poetry_install_extras(extras)
    torch_ok = _install_torch(vendor, platform)
    _verify_torch(vendor)
    if not torch_ok and vendor != "cpu":
        print(
            _(
                "[setup] ⚠ The GPU torch install failed (see the error above). "
                "Without it the app falls back to CPU. Fix it and run `make configure`."
            ),
            file=sys.stderr,
        )
    if backend == "whispercpp":
        if platform == "windows":
            _install_whispercpp()
        else:
            _install_whispercpp_linux(interactive)

    # CTranslate2-ROCm (faster-whisper on the AMD GPU — quality identical to mainline).
    # Heavy, opt-in build; failure does NOT abort (the chain falls back to whisper.cpp).
    ct2_rocm_ok: bool | None = saved.ct2_rocm_ok
    if vendor == "amd" and platform != "windows":
        ct2_rocm_ok = _maybe_install_ct2_rocm(info, saved, interactive)

    if _prompt_yes_no(_("\nSave these choices for the next run?"), True, interactive):
        save_persisted(
            PersistedConfig(
                gpu_vendor=vendor,
                whisper_backend=backend,
                tts_enabled=tts_enabled,
                tts_engine=tts_engine,
                default_flow=flow,
                platform=platform,
                trigger=trigger,
                stt_strategy=saved.stt_strategy or "auto",
                ct2_rocm_ok=ct2_rocm_ok,
                daemon_port=saved.daemon_port,
            )
        )
        print(_("[setup] ✓ Choices saved to ~/.config/voicemate/config.toml"))
    else:
        print(_("[setup] Choices not saved (apply to this install only)."))

    if platform == "wsl2":
        _maybe_install_systemd_unit(interactive)

    print(_("\n[setup] Done! Run:  make run"))
    print(_("[setup] Environment diagnostics (audio/mic/hotkeys):  make doctor"))
    if platform == "wsl2":
        print(_("[setup] WSL2: register the hotkeys on the Windows side — see docs/wsl2.md."))
    return 0


def _maybe_install_systemd_unit(interactive: bool) -> None:
    """Install the systemd user service (daemon autostart on WSL2). Opt-in."""
    template = Path(__file__).resolve().parents[2] / "scripts" / "systemd" / "voicemate.service"
    if not template.exists():
        return
    if not _prompt_yes_no(_("\nInstall the systemd service (daemon starts with WSL)?"), False, interactive):
        print(_("[setup] systemd service not installed (instructions in docs/wsl2.md)."))
        return
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    content = template.read_text(encoding="utf-8").replace(
        "WorkingDirectory=%h/voice-mate", f"WorkingDirectory={repo_root}"
    )
    (unit_dir / "voicemate.service").write_text(content, encoding="utf-8")
    ok = _run(["systemctl", "--user", "daemon-reload"], _("Reloading systemd units (user)..."))
    if ok:
        _run(["systemctl", "--user", "enable", "--now", "voicemate"], _("Enabling the voicemate service..."))
        print(_("[setup] ✓ Service installed. Logs: journalctl --user -u voicemate -f"))
        print(_("[setup]   To survive without an open session: loginctl enable-linger $USER"))


def _maybe_install_ct2_rocm(info: GpuInfo, saved: PersistedConfig, interactive: bool) -> bool | None:
    from app.setup import ct2_rocm

    if saved.ct2_rocm_ok is True and ct2_rocm.is_installed():
        print(_("[setup] CTranslate2-ROCm already installed and validated (reusing)."))
        return True
    default_try = saved.ct2_rocm_ok is not False  # already failed before → default No
    wants = _prompt_yes_no(
        _(
            "\nInstall CTranslate2-ROCm? (faster-whisper on the AMD GPU — maximum quality;\n"
            "SLOW build from source. If you skip it or it fails, whisper.cpp takes over.)"
        ),
        default_try,
        interactive,
    )
    if not wants:
        print(_("[setup] CT2-ROCm skipped (whisper.cpp will be the transcription backend)."))
        return saved.ct2_rocm_ok
    ok = ct2_rocm.install(info.gfx_target)
    if not ok:
        print(
            _(
                "[setup] ⚠ CT2-ROCm failed — continuing with whisper.cpp (without aborting). "
                "Fix the prerequisites and run `make configure` to retry."
            ),
            file=sys.stderr,
        )
    return ok


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gpu_bootstrap",
        description=_("Detect the GPU, install the right torch (CUDA/ROCm/CPU) and remember the choice."),
    )
    parser.add_argument("--vendor", choices=["nvidia", "amd", "cpu"], default=None, help=_("Skip detection/prompt."))
    parser.add_argument("--yes", action="store_true", help=_("Non-interactive: accept the defaults (detected/saved)."))
    parser.add_argument("--reconfigure", action="store_true", help=_("Re-ask the choices (used by make configure)."))
    parser.add_argument("--extras", default=None, help=_('Override the poetry extras (e.g. "all").'))
    return parser.parse_args(argv)


def _compute_extras(
    flow: FlowKind, tts_enabled: bool, vendor: GpuVendor, platform: PlatformKind, tts_engine: TTSEngine
) -> set[str]:
    extras: set[str] = set()
    if flow == "claude_chat":
        extras.add("claude")
        if tts_enabled:
            # Each engine has its own poetry extra (kokoro=light/CPU, tts=omnivoice,
            # voxcpm=alternative). "tts" is the default for engines without their own extra.
            extras.add({"kokoro": "kokoro", "voxcpm": "voxcpm"}.get(tts_engine, "tts"))
    # AMD on Linux/WSL2: openai-whisper (on top of ROCm torch) is the reliable GPU
    # transcription backend — on WSL2 whisper.cpp's Vulkan only sees llvmpipe
    # (software CPU), so the chain needs it installed.
    if vendor == "amd" and platform != "windows":
        extras.add("whisper-gpu")
    return extras


def _prompt_vendor(default: GpuVendor, interactive: bool) -> GpuVendor:
    if not interactive:
        return default
    labels: dict[GpuVendor, str] = {
        "nvidia": _("NVIDIA (CUDA — faster-whisper on the GPU)"),
        "amd": _("AMD (ROCm — VoxCPM on the GPU + openai-whisper)"),
        "cpu": _("CPU (no GPU)"),
    }
    order: list[GpuVendor] = ["nvidia", "amd", "cpu"]
    print(_("\nWhich configuration to install?"))
    for i, vendor in enumerate(order, 1):
        mark = _("  (detected/suggested)") if vendor == default else ""
        print(f"  {i}) {labels[vendor]}{mark}")
    raw = _ask(_("Choose [1-3] (Enter = {default}): ").format(default=default)).lower()
    mapping: dict[str, GpuVendor] = {
        "1": "nvidia",
        "2": "amd",
        "3": "cpu",
        "nvidia": "nvidia",
        "amd": "amd",
        "cpu": "cpu",
    }
    return mapping.get(raw, default)


def _prompt_tts_engine(default: TTSEngine, interactive: bool) -> TTSEngine:
    if not interactive:
        return default
    print(_("\nWhich TTS engine (voice of Claude's response)?"))
    print(_("  1) kokoro — light, runs on the CPU, realtime, does NOT saturate the GPU (no crackle). Fixed voices."))
    print(_("  2) omnivoice — clones a voice, high quality, but heavy on the GPU (may crackle on WSL2)."))
    raw = _ask(_("Choose [1-2] (Enter = {default}): ").format(default=default)).lower()
    mapping: dict[str, TTSEngine] = {"1": "kokoro", "2": "omnivoice", "kokoro": "kokoro", "omnivoice": "omnivoice"}
    return mapping.get(raw, default)


def _prompt_flow(interactive: bool, default: FlowKind) -> FlowKind:
    if not interactive:
        return default
    print(_("\nWhich main flow?"))
    print(_("  1) clipboard — voice becomes text on the clipboard (transcription only)"))
    print(_("  2) claude_chat — voice → Claude → spoken response (+ clipboard)"))
    raw = _ask(_("Choose [1-2] (Enter = {default}): ").format(default=default)).lower()
    mapping: dict[str, FlowKind] = {
        "1": "clipboard",
        "2": "claude_chat",
        "clipboard": "clipboard",
        "claude_chat": "claude_chat",
    }
    return mapping.get(raw, default)


def _prompt_yes_no(question: str, default_yes: bool, interactive: bool) -> bool:
    if not interactive:
        return default_yes
    suffix = _("[Y/n]") if default_yes else _("[y/N]")
    raw = _ask(f"{question} {suffix} ").lower()
    if not raw:
        return default_yes
    return raw in ("s", "sim", "y", "yes")


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _linux_rocm_torch_already_ok() -> bool:
    """True if the venv already has a +rocm torch accelerating — don't overwrite.

    Protects a manually validated install (e.g. wheels from repo.radeon.com)
    from a --force-reinstall that would swap one tested build for another.
    """
    code = "import torch; print(torch.__version__); print(torch.cuda.is_available())"
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return False
    lines = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or len(lines) < 2:
        return False
    version, accelerated = lines[0], lines[1].strip().lower() == "true"
    return "+rocm" in version and accelerated


def _cleanup_libhsa() -> None:
    """Remove the libhsa-runtime64 bundled inside torch (official AMD step for WSL).

    On WSL the correct HSA runtime comes from the system (wsl usecase); the copy
    inside the wheel can shadow it. In recent releases the file doesn't even
    exist — absence is OK, the step is kept as a safeguard for other versions.
    """
    code = "import torch, pathlib; print(pathlib.Path(torch.__file__).parent / 'lib')"
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return
    lib_dir = Path((proc.stdout or "").strip())
    if proc.returncode != 0 or not lib_dir.is_dir():
        return
    for lib in lib_dir.glob("libhsa-runtime64.so*"):
        try:
            lib.unlink()
            print(_("[setup] Removed {name} from torch (the WSL HSA runtime is the system's).").format(name=lib.name))
        except OSError:
            pass


def _install_torch(vendor: GpuVendor, platform: PlatformKind) -> bool:
    pip = [sys.executable, "-m", "pip", "install"]
    if vendor == "amd" and platform != "windows":
        # Linux/WSL2: manylinux wheels from repo.radeon.com (the combination that
        # AMD tests for WSL: torch+torchvision+torchaudio+triton + numpy<2).
        if sys.version_info[:2] != (3, 12):
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            print(
                _(
                    "[setup] ⚠ The ROCm Linux wheels are cp312 (this Python is {ver}). "
                    "Recreate the environment with 3.12 (`poetry env use 3.12`) and run `make configure`."
                ).format(ver=ver),
                file=sys.stderr,
            )
            return False
        if _linux_rocm_torch_already_ok():
            print(_("[setup] ✓ ROCm torch already present and accelerating — keeping the validated install."))
            print(_("[setup]   (to force a reinstall: pip uninstall torch and run `make configure`)"))
            return True
        ok = _run(
            pip + ["--force-reinstall", _ROCM_LINUX_NUMPY_PIN, *_ROCM_LINUX_WHEELS],
            _("Installing ROCm torch (official AMD wheels for Linux/WSL2)..."),
        )
        if ok:
            _cleanup_libhsa()
        return ok
    if vendor == "amd":
        # AMD's ROCm wheels are cp312-only — on a different Python pip rejects
        # them ("not a supported wheel on this platform") and only the CPU torch
        # would remain. Fail early and clearly instead of a half-installed GPU.
        if sys.version_info[:2] != (3, 12):
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            print(
                _(
                    "[setup] ⚠ The ROCm wheels require Python 3.12 (this is {ver}). "
                    "Recreate the environment with 3.12 (`poetry env use 3.12`) and run `make configure`."
                ).format(ver=ver),
                file=sys.stderr,
            )
            return False
        # 1) ROCm SDK runtime (without it ROCm torch won't even import).
        ok_sdk = _run(pip + ["--no-cache-dir", *_ROCM_SDK_WHEELS], _("Installing ROCm SDK (AMD runtime)..."))
        # 2) torch+torchaudio ROCm: --force-reinstall beats the CPU torch that
        #    poetry pulled in; --no-deps stops pip from swapping it for a PyPI CPU one.
        ok_torch = _run(
            pip + ["--no-cache-dir", "--force-reinstall", "--no-deps", *_ROCM_WHEELS],
            _("Installing ROCm torch (AMD)..."),
        )
        return ok_sdk and ok_torch
    index = _TORCH_INDEX[vendor]
    label = _("Installing CUDA torch (NVIDIA)...") if vendor == "nvidia" else _("Installing CPU torch...")
    return _run(pip + ["--force-reinstall", "--index-url", index, "torch", "torchaudio"], label)


def _poetry_install_extras(extras: set[str]) -> bool:
    cmd = ["poetry", "install"]
    if extras:
        cmd += ["--extras", " ".join(sorted(extras))]
    return _run(cmd, _("Installing dependencies (poetry)..."))


def _verify_torch(vendor: GpuVendor) -> None:
    code = "import torch; print('torch', torch.__version__); print('cuda', torch.cuda.is_available())"
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(_("[setup] ⚠ Could not verify torch: {exc}").format(exc=exc), file=sys.stderr)
        return
    out = (proc.stdout or "").strip()
    if out:
        print(_("[setup] torch check:"))
        print("  " + out.replace("\n", "\n  "))
    accelerated = "cuda true" in out.lower()
    if vendor in ("nvidia", "amd") and not accelerated:
        print(
            _(
                "[setup] ⚠ The GPU was not recognized by torch — the app will fall back to CPU. "
                "Check the driver and run `make configure`."
            ),
            file=sys.stderr,
        )
    elif accelerated:
        print(_("[setup] ✓ GPU ready for torch."))


def _install_whispercpp() -> bool:
    """Download whisper.cpp + Vulkan (binary) and the GGUF model, with a pinned SHA-256."""
    _WHISPERCPP_DIR.mkdir(parents=True, exist_ok=True)
    exe = _WHISPERCPP_DIR / "whisper-cli.exe"
    model = _WHISPERCPP_DIR / _WCPP_MODEL_NAME
    if exe.exists() and model.exists():
        print(_("[setup] whisper.cpp already present in {dir}.").format(dir=_WHISPERCPP_DIR))
        return True
    print(_("[setup] Installing whisper.cpp + Vulkan (binary ~17 MB + turbo fp16 model ~1.5 GB)..."))
    if not exe.exists():
        zip_path = _WHISPERCPP_DIR / "_whispercpp.zip"
        if not _download_verified(_WCPP_BIN_URL, zip_path, _WCPP_BIN_SHA256):
            return False
        _extract_whispercpp(zip_path)
        zip_path.unlink(missing_ok=True)
    if not model.exists() and not _download_verified(_WCPP_MODEL_URL, model, _WCPP_MODEL_SHA256):
        return False
    print(_("[setup] ✓ whisper.cpp ready in {dir}").format(dir=_WHISPERCPP_DIR))
    return True


def _install_whispercpp_linux(interactive: bool) -> bool:
    """Build whisper.cpp (Vulkan, static binary) and download the model + VAD.

    Vulkan is the default backend on Linux/WSL2 today: HIP for gfx120X is still
    in a PR on whisper.cpp (ggml-org/whisper.cpp#3757). Once it merges, the cmake
    flag can be switched to -DGGML_HIP=ON.
    """
    _WHISPERCPP_DIR.mkdir(parents=True, exist_ok=True)
    cli = _WHISPERCPP_DIR / "whisper-cli"
    server = _WHISPERCPP_DIR / "whisper-server"
    model = _WHISPERCPP_DIR / _WCPP_MODEL_NAME
    vad = _WHISPERCPP_DIR / _WCPP_VAD_NAME

    binaries_ok = cli.exists() and server.exists()
    if not binaries_ok:
        missing = [tool for tool in ("git", "cmake", "g++", "glslc", "glslangValidator") if shutil.which(tool) is None]
        if missing:
            print(
                _("[setup] ⚠ Missing tools to compile whisper.cpp: {tools}").format(tools=", ".join(missing)),
                file=sys.stderr,
            )
            print(_("[setup]   Install with: {hint}").format(hint=_WCPP_LINUX_APT_HINT), file=sys.stderr)
            return False
        binaries_ok = _build_whispercpp_linux()
        if not binaries_ok:
            return False

    ok = True
    if not model.exists():
        # fp16 turbo (reference quality). Q8_0 stays available for anyone who
        # wants to save VRAM (download manually; find_model picks up any ggml-*).
        ok = _download_verified(_WCPP_MODEL_URL, model, _WCPP_MODEL_SHA256) and ok
    if not vad.exists():
        ok = _download_verified(_WCPP_VAD_URL, vad, _WCPP_VAD_SHA256) and ok
    if ok:
        print(_("[setup] ✓ whisper.cpp (Linux/Vulkan) ready in {dir}").format(dir=_WHISPERCPP_DIR))
    return ok


def _build_whispercpp_linux() -> bool:
    src = _WHISPERCPP_DIR / "src"
    build = src / "build"
    if not (src / ".git").exists():
        if not _run(
            ["git", "clone", "--depth", "1", "--branch", _WCPP_LINUX_TAG, _WCPP_LINUX_REPO, str(src)],
            _("Cloning whisper.cpp {tag}...").format(tag=_WCPP_LINUX_TAG),
        ):
            return False
    if not _run(
        [
            "cmake",
            "-S",
            str(src),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DGGML_VULKAN=1",
            "-DBUILD_SHARED_LIBS=OFF",
            "-DWHISPER_BUILD_TESTS=OFF",
        ],
        _("Configuring the whisper.cpp build (cmake + Vulkan)..."),
    ):
        return False
    jobs = str(max(1, (os.cpu_count() or 4) - 1))
    if not _run(
        ["cmake", "--build", str(build), "--parallel", jobs],
        _("Compiling whisper.cpp (a few minutes)..."),
    ):
        return False
    copied = 0
    for name in ("whisper-cli", "whisper-server"):
        built = build / "bin" / name
        if built.exists():
            target = _WHISPERCPP_DIR / name
            shutil.copy2(built, target)
            target.chmod(0o755)
            copied += 1
        else:
            print(_("[setup] ⚠ Binary not found after the build: {path}").format(path=built), file=sys.stderr)
    return copied == 2


def _extract_whispercpp(zip_path: Path) -> None:
    """Extract only the CLIs + DLLs (basename only → no path traversal)."""
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = Path(info.filename).name
            if name in _WCPP_KEEP_EXACT or name.endswith(".dll"):
                (_WHISPERCPP_DIR / name).write_bytes(archive.read(info))


def _download_verified(url: str, dest: Path, expected_sha256: str) -> bool:
    print(_("[setup] downloading {url}").format(url=url))
    try:
        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        print()
    except OSError as exc:
        print(_("[setup] ⚠ download failed: {exc}").format(exc=exc), file=sys.stderr)
        return False
    digest = _sha256_file(dest)
    if digest.lower() != expected_sha256.lower():
        print(
            _("[setup] ⚠ SHA-256 mismatch for {name} (got {digest}); removing.").format(name=dest.name, digest=digest),
            file=sys.stderr,
        )
        dest.unlink(missing_ok=True)
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _progress(block_num: int, block_size: int, total_size: int) -> None:
    if total_size <= 0 or block_num % 256 != 0:  # print ~every 2 MB
        return
    done_mb = block_num * block_size // (1024 * 1024)
    total_mb = total_size // (1024 * 1024)
    print(f"\r[setup]   {done_mb}/{total_mb} MB", end="", flush=True)


def _run(cmd: list[str], desc: str) -> bool:
    print(f"\n[setup] {desc}")
    print("[setup] $ " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd)
    except OSError as exc:
        print(_("[setup] ⚠ Failed to execute: {exc}").format(exc=exc), file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(_("[setup] ⚠ Command returned code {code}.").format(code=proc.returncode), file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
