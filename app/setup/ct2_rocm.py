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

_APT_HINT = (
    "sudo apt install -y git cmake build-essential rocm-hip-sdk miopen-hip-dev"
    "  # rocm-hip-sdk = hipcc + hipBLAS; miopen-hip-dev = Whisper's convolutions on the GPU"
)


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
    missing = [label for label, exe in tools.items() if shutil.which(exe) is None]
    if "hipcc (ROCm)" not in missing and not (_rocm_root() / "include" / "miopen" / "miopen.h").exists():
        missing.append("MIOpen (miopen-hip-dev)")  # the build needs it since Conv1D runs on MIOpen
    return missing


def _clone() -> bool:
    if (_SRC_DIR / ".git").exists():
        _log(_("Source already cloned at {path} (reusing).").format(path=_SRC_DIR))
        return True
    return _run(
        ["git", "clone", "--recursive", "--depth", "1", _REPO_URL, str(_SRC_DIR)],
        _("Cloning CTranslate2-rocm (with submodules)..."),
    )


def _rocm_compilers() -> dict[str, str]:
    """The ROCm clang for the C++ and HIP sources, as the fork's README builds it. Without it
    CMake picks the system `c++` (gcc), which stops at "language hip not recognized"."""
    found: dict[str, str] = {}
    try:
        llvm = subprocess.run(["hipconfig", "-l"], capture_output=True, text=True, timeout=30).stdout.strip()
        rocm = subprocess.run(["hipconfig", "-R"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return found
    if llvm and (Path(llvm) / "clang++").exists():
        found["CXX"] = str(Path(llvm) / "clang++")
        found["HIPCXX"] = str(Path(llvm) / "clang")
    if rocm:
        found["HIP_PATH"] = rocm
    return found


# The fork was written against ROCm 6: ROCm 7 renamed the hipBLAS `_v2` calls back to their plain
# names and its rocThrust no longer pulls these headers in through `execution_policy.h`.
_ROCM7_HEADERS = "".join(
    f"#include <thrust/{header}>\n"
    for header in (
        "iterator/counting_iterator.h",
        "iterator/transform_iterator.h",
        "reduce.h",
        "extrema.h",
        "functional.h",
        "transform.h",
        "fill.h",
        "sequence.h",
        "gather.h",
        "scatter.h",
    )
)
_ROCM7_MARK = "// VoiceMate: ROCm 7 headers\n"


def _patch_for_rocm7(src_dir: Path) -> None:
    """Make the fork build on ROCm 6 and 7 alike (idempotent: a rebuild finds it patched)."""
    utils = src_dir / "src" / "cuda" / "utils.h"
    text = utils.read_text(encoding="utf-8")
    anchor = "#include <thrust/execution_policy.h>\n"
    if _ROCM7_MARK not in text and anchor in text:
        utils.write_text(text.replace(anchor, anchor + _ROCM7_MARK + _ROCM7_HEADERS, 1), encoding="utf-8")
    primitives = src_dir / "src" / "cuda" / "primitives.cu"
    text = primitives.read_text(encoding="utf-8")
    if _hipblas_major() >= 3:  # hipBLAS 3 (ROCm 7) has no `_v2` names left
        patched = text.replace("hipblasGemmEx_v2", "hipblasGemmEx").replace(
            "hipblasGemmStridedBatchedEx_v2", "hipblasGemmStridedBatchedEx"
        )
        if patched != text:
            primitives.write_text(patched, encoding="utf-8")


def _rocm_root() -> Path:
    """ROCm's install folder (`hipconfig -R`), /opt/rocm when hipconfig does not say."""
    try:
        rocm = subprocess.run(["hipconfig", "-R"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        rocm = ""
    return Path(rocm or "/opt/rocm")


def _hipblas_major() -> int:
    """hipBLAS's major version from its header (0 when unknown)."""
    header = _rocm_root() / "include" / "hipblas" / "hipblas-version.h"
    try:
        for line in header.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[:2] == ["#define", "hipblasVersionMajor"]:
                return int(parts[2])
    except (OSError, ValueError):
        pass
    return 0


def _drop_stale_cache(cxx: str) -> None:
    """CMake keeps the compiler of its first run: a build dir configured with another one (an
    earlier attempt with gcc) is started over, or `make configure` would fail the same way."""
    cache = _BUILD_DIR / "CMakeCache.txt"
    if not cxx or not cache.exists():
        return
    if f"CMAKE_CXX_COMPILER:FILEPATH={cxx}\n" not in cache.read_text(encoding="utf-8", errors="replace"):
        shutil.rmtree(_BUILD_DIR)


def _build(gfx_target: str | None) -> bool:
    env = dict(os.environ)
    for name, value in _rocm_compilers().items():
        env.setdefault(name, value)
    # Embedded rpath: the python wrapper finds the lib without a global LD_LIBRARY_PATH, and the
    # lib finds the OpenMP runtime of the ROCm clang (libomp.so), which is outside the linker's path.
    rpath = [str(_PREFIX_DIR / "lib")]
    if env.get("CXX"):
        rpath.append(str(Path(env["CXX"]).parent.parent / "lib"))
    try:
        _patch_for_rocm7(_SRC_DIR)
        _drop_stale_cache(env.get("CXX", ""))
    except OSError as exc:
        _log(_("⚠ Failed to execute: {exc}").format(exc=exc))
        return False
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
        "-DWITH_CUDNN=ON",  # MIOpen on ROCm: Whisper's Conv1D needs it on the GPU
        "-DBUILD_TESTS=OFF",
        "-DOPENMP_RUNTIME=COMP",
        f"-DCMAKE_INSTALL_PREFIX={_PREFIX_DIR}",
        f"-DCMAKE_INSTALL_RPATH={';'.join(rpath)}",
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
    # Its setup.py imports pybind11 before declaring anything, so pip's isolated build env never
    # has it: install the wrapper's own build requirements here and build without isolation.
    if not _run(
        [sys.executable, "-m", "pip", "install", "-r", str(_SRC_DIR / "python" / "install_requirements.txt")],
        _("Installing the build requirements of the python wrapper..."),
        env=env,
    ):
        return False
    return _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-build-isolation",
            "--force-reinstall",
            # Its dependencies (numpy, pyyaml) are already in the venv at the locked versions; a
            # forced reinstall would upgrade numpy past what numba (openai-whisper) accepts.
            "--no-deps",
            str(_SRC_DIR / "python"),
        ],
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
