"""Build + install the ROCm fork of CTranslate2 (faster-whisper on the AMD GPU).

The official CTranslate2 has no ROCm backend (and hangs on gfx1201); the
`arlo-phoenix/CTranslate2-rocm` fork compiles the "CUDA" backend via HIP, so
faster-whisper gets GPU acceleration on AMD with `device="cuda"` — quality
identical to mainline on NVIDIA.

Build from source (takes a while — tens of minutes to hours). Everything here
is best-effort: any failure prints the reason + the fix and returns False, and
the caller (gpu_bootstrap) persists `ct2_rocm_ok=false` and continues to
whisper.cpp WITHOUT aborting the setup. `make configure` retries.

Stdlib only — runs at install time.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.i18n import _

_REPO_URL = "https://github.com/arlo-phoenix/CTranslate2-rocm"
_CACHE_DIR = Path.home() / ".cache" / "voicemate" / "ct2-rocm"
_SRC_DIR = _CACHE_DIR / "src"
_BUILD_DIR = _SRC_DIR / "build"
_PREFIX_DIR = _CACHE_DIR / "prefix"
_MARKER_PATH = _CACHE_DIR / "installed.json"

_APT_HINT = "sudo apt install -y git cmake build-essential rocm-hip-sdk  # rocm-hip-sdk = hipcc + hipBLAS"


def is_installed() -> bool:
    return _MARKER_PATH.exists()


def install(gfx_target: str | None, interactive_log: bool = True) -> bool:
    """Clone, build, install the python wrapper and validate. False on any failure."""
    missing = _missing_tools()
    if missing:
        _log(_("⚠ Missing tools for the CT2-ROCm build: {tools}").format(tools=", ".join(missing)))
        _log(_("  Install with: {hint}").format(hint=_APT_HINT))
        _log(_("  (on WSL2, also install ROCm: https://rocm.docs.amd.com)"))
        return False
    if gfx_target is None:
        _log(_("⚠ GPU gfx target not detected (rocminfo); using the fork's default list."))

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not _clone():
        return False
    if not _build(gfx_target):
        return False
    if not _pip_install_wrapper():
        return False
    if not verify():
        _log(_("⚠ Build completed but validation failed (import/GPU). See messages above."))
        return False

    _MARKER_PATH.write_text(
        json.dumps({"gfx_target": gfx_target, "prefix": str(_PREFIX_DIR)}, indent=2) + "\n",
        encoding="utf-8",
    )
    _log(_("✓ CTranslate2-ROCm installed and validated (faster-whisper accelerates on the AMD GPU)."))
    return True


def verify() -> bool:
    """Validate in a subprocess: import + at least 1 visible 'cuda' (HIP) device."""
    code = (
        "import ctranslate2, sys; "
        "n = ctranslate2.get_cuda_device_count(); "
        "print('ct2', ctranslate2.__version__, 'gpu_devices', n); "
        "sys.exit(0 if n > 0 else 1)"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            env=runtime_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log(_("⚠ CT2-ROCm validation did not run: {exc}").format(exc=exc))
        return False
    out = (proc.stdout or "").strip()
    if out:
        _log(f"  {out}")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-400:]
        if tail:
            _log(f"  stderr: {tail}")
        return False
    return True


def runtime_env() -> dict[str, str]:
    """Env with LD_LIBRARY_PATH pointing to the libctranslate2 installed in the prefix."""
    env = dict(os.environ)
    lib_dir = str(_PREFIX_DIR / "lib")
    current = env.get("LD_LIBRARY_PATH", "")
    if lib_dir not in current.split(":"):
        env["LD_LIBRARY_PATH"] = f"{lib_dir}:{current}" if current else lib_dir
    # Same workaround as the runtime (wiring): CT2's default allocator gives a
    # "Memory access fault" on gfx1201 — validate with the right allocator already.
    env.setdefault("CT2_CUDA_ALLOCATOR", "cub_caching")
    return env


def _missing_tools() -> list[str]:
    tools = {
        "git": "git",
        "cmake": "cmake",
        "hipcc (ROCm)": "hipcc",
    }
    return [label for label, exe in tools.items() if shutil.which(exe) is None]


def _clone() -> bool:
    if (_SRC_DIR / ".git").exists():
        _log(_("Source already cloned at {path} (reusing).").format(path=_SRC_DIR))
        return True
    return _run(
        ["git", "clone", "--recursive", "--depth", "1", _REPO_URL, str(_SRC_DIR)],
        _("Cloning CTranslate2-rocm (with submodules)..."),
    )


def _build(gfx_target: str | None) -> bool:
    env = dict(os.environ)
    if gfx_target:
        # The HIP build uses GPU_TARGETS/CMAKE_HIP_ARCHITECTURES to compile kernels
        # only for the local architecture (faster, smaller build).
        env.setdefault("GPU_TARGETS", gfx_target)
        env.setdefault("PYTORCH_ROCM_ARCH", gfx_target)
    configure = [
        "cmake",
        "-S",
        str(_SRC_DIR),
        "-B",
        str(_BUILD_DIR),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DWITH_MKL=OFF",
        "-DWITH_HIP=ON",
        "-DWITH_CUDNN=OFF",
        "-DBUILD_TESTS=OFF",
        "-DOPENMP_RUNTIME=COMP",
        f"-DCMAKE_INSTALL_PREFIX={_PREFIX_DIR}",
        # Embedded rpath: the python wrapper finds the lib without a global LD_LIBRARY_PATH.
        f"-DCMAKE_INSTALL_RPATH={_PREFIX_DIR / 'lib'}",
    ]
    if gfx_target:
        configure.append(f"-DCMAKE_HIP_ARCHITECTURES={gfx_target}")
    if not _run(configure, _("Configuring build (cmake)..."), env=env):
        return False
    jobs = str(max(1, (os.cpu_count() or 4) - 1))
    if not _run(
        ["cmake", "--build", str(_BUILD_DIR), "--parallel", jobs],
        _("Compiling (parallel={jobs}; this can take a LONG time — grab a coffee ☕)...").format(jobs=jobs),
        env=env,
    ):
        return False
    return _run(
        ["cmake", "--install", str(_BUILD_DIR)],
        _("Installing into {path}...").format(path=_PREFIX_DIR),
        env=env,
    )


def _pip_install_wrapper() -> bool:
    env = dict(os.environ)
    env["CTRANSLATE2_ROOT"] = str(_PREFIX_DIR)
    # rpath in the wrapper's native module → import works without LD_LIBRARY_PATH.
    env["LDFLAGS"] = f"-Wl,-rpath,{_PREFIX_DIR / 'lib'} " + env.get("LDFLAGS", "")
    return _run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall", str(_SRC_DIR / "python")],
        _("Installing the CTranslate2-ROCm python wrapper into the venv..."),
        env=env,
    )


def _run(cmd: list[str], desc: str, env: dict[str, str] | None = None) -> bool:
    _log(desc)
    _log("$ " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, env=env)
    except OSError as exc:
        _log(_("⚠ Failed to execute: {exc}").format(exc=exc))
        return False
    if proc.returncode != 0:
        _log(_("⚠ Command returned code {code}.").format(code=proc.returncode))
        return False
    return True


def _log(message: str) -> None:
    print(f"[setup:ct2-rocm] {message}", flush=True)
