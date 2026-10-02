"""Detect the GPU vendor (NVIDIA / AMD / CPU) without heavy dependencies.

Covers Windows (WMI via PowerShell) and Linux/WSL2 (rocm-smi → amd-smi → lspci).
Degrades gracefully on any OS: every external call is wrapped so a
missing tool / timeout / error just falls through to the next check, ending at
the always-safe "cpu" default.

Only stdlib is used here — this module is imported at install time, before
torch exists.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config import GpuVendor
from app.i18n import _

# Minimum Adrenalin driver version for ROCm (Windows and WSL2).
# Only used in a warning message — the real check is fragile (see detect_amd).
REQUIRED_AMD_DRIVER = "26.2.2"

_AMD_DRIVER_URL = "https://www.amd.com/en/support"

_SUBPROCESS_TIMEOUT = 5.0

_GFX_RE = re.compile(r"\bgfx[0-9a-f]{3,}\b")


@dataclass
class GpuInfo:
    """Result of GPU detection."""

    vendor: GpuVendor
    device_name: str | None = None
    driver_version: str | None = None
    # True when usable HIP/ROCm tooling is present; None/False = can't tell,
    # so we only warn (we never block the install over it).
    rocm_driver_ok: bool = False
    # AMD GPU architecture (e.g. "gfx1201" on the RX 9070 XT) via rocminfo —
    # used as GPU_TARGETS in the CTranslate2-ROCm build.
    gfx_target: str | None = None


def _run(cmd: list[str]) -> str | None:
    """Run a command and return stdout (stripped), or None on any failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


def detect_nvidia() -> str | None:
    """Return the NVIDIA GPU name via `nvidia-smi`, or None."""
    if shutil.which("nvidia-smi") is None:
        return None
    out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if not out:
        return None
    # First line = first GPU.
    return out.splitlines()[0].strip()


def detect_amd() -> tuple[str | None, str | None]:
    """Return (name, driver_version) of the AMD GPU, or (None, None)."""
    if sys.platform == "win32":
        return _detect_amd_windows()
    return _detect_amd_linux()


def _detect_amd_windows() -> tuple[str | None, str | None]:
    """AMD on Windows via WMI/PowerShell.

    WARNING: WMI's `DriverVersion` is the .inf version (e.g. 32.0.x), NOT the
    Adrenalin version (e.g. 26.2.2). It is for display only.
    """
    if shutil.which("powershell") is None:
        return (None, None)
    out = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion | ConvertTo-Json -Compress",
        ]
    )
    if not out:
        return (None, None)
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return (None, None)
    controllers = data if isinstance(data, list) else [data]
    for ctrl in controllers:
        name = str(ctrl.get("Name", ""))
        if _looks_amd(name):
            return (name, ctrl.get("DriverVersion"))
    return (None, None)


def _detect_amd_linux() -> tuple[str | None, str | None]:
    """AMD on Linux/WSL2: rocminfo → rocm-smi → amd-smi → lspci.

    rocminfo comes first on purpose: it is the only tool that ALSO works on
    WSL2 (rocm-smi/amd-smi don't operate there due to an architecture limitation),
    and its "Marketing Name" carries the exact commercial name (e.g. "AMD Radeon
    RX 9070 XT") — lspci, the last fallback, shows only the chip name.
    """
    if shutil.which("rocminfo") is not None:
        name = _parse_rocminfo_marketing_name(_run(["rocminfo"]) or "")
        if name:
            return (name, _amdgpu_kernel_version())
    if shutil.which("rocm-smi") is not None:
        name = _parse_rocm_smi_product(_run(["rocm-smi", "--showproductname"]) or "")
        if name:
            return (name, _amdgpu_kernel_version())
    if shutil.which("amd-smi") is not None:
        name = _parse_amd_smi_asic(_run(["amd-smi", "static", "--asic"]) or "")
        if name:
            return (name, _amdgpu_kernel_version())
    if shutil.which("lspci") is not None:
        name = _parse_lspci_amd(_run(["lspci"]) or "")
        if name:
            return (name, _amdgpu_kernel_version())
    return (None, None)


def _parse_rocminfo_marketing_name(out: str) -> str | None:
    """The GPU's commercial name in the `rocminfo` output.

    rocminfo lists CPU and GPU agents, both with a "Marketing Name"; we filter
    for what looks like a video card (Radeon/Instinct/Graphics) so we don't return
    the Ryzen's name.
    """
    for line in out.splitlines():
        if "marketing name" not in line.lower():
            continue
        value = line.split(":", 1)[-1].strip()
        low = value.lower()
        if value and ("radeon" in low or "instinct" in low or "graphics" in low):
            return value
    return None


def _parse_rocm_smi_product(out: str) -> str | None:
    """Extract the card name from `rocm-smi --showproductname` output."""
    for line in out.splitlines():
        if "card series" in line.lower() or "card model" in line.lower():
            value = line.split(":")[-1].strip()
            if value:
                return value
    return None


def _parse_amd_smi_asic(out: str) -> str | None:
    """Extract MARKET_NAME from `amd-smi static --asic` output."""
    for line in out.splitlines():
        if "market_name" in line.lower():
            value = line.split(":")[-1].strip()
            if value:
                return value
    return None


_LSPCI_AMD_RE = re.compile(r"\b(amd|ati|radeon)\b", re.IGNORECASE)


def _parse_lspci_amd(out: str) -> str | None:
    """Find the AMD VGA/Display line in `lspci` and return its description.

    Word boundary required: "Corporation" contains "ati" — without \\b NVIDIA
    would pass the filter.
    """
    for line in out.splitlines():
        low = line.lower()
        if ("vga" in low or "display" in low or "3d controller" in low) and _LSPCI_AMD_RE.search(line):
            # Format: "03:00.0 VGA compatible controller: <description>"
            return line.split(":", 2)[-1].strip()
    return None


def _amdgpu_kernel_version() -> str | None:
    try:
        return Path("/sys/module/amdgpu/version").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def detect_gfx_target() -> str | None:
    """AMD GPU gfx architecture via `rocminfo` (e.g. gfx1201). None if unavailable."""
    if shutil.which("rocminfo") is None:
        return None
    out = _run(["rocminfo"])
    if not out:
        return None
    return _parse_gfx_target(out)


def _parse_gfx_target(out: str) -> str | None:
    """First gfx* target in the rocminfo output (the GPU; CPUs have no gfx)."""
    match = _GFX_RE.search(out)
    return match.group(0) if match else None


def _rocm_tooling_ok() -> bool:
    if sys.platform == "win32":
        return shutil.which("hipInfo") is not None or shutil.which("hipconfig") is not None
    return shutil.which("rocminfo") is not None or Path("/dev/kfd").exists()


def _looks_amd(name: str) -> bool:
    low = name.lower()
    return "amd" in low or "radeon" in low or low.startswith("rx ") or " rx " in low


def detect_gpu() -> GpuInfo:
    """Detect the GPU vendor. Order: NVIDIA → AMD → CPU (safe fallback)."""
    nvidia_name = detect_nvidia()
    if nvidia_name:
        return GpuInfo(vendor="nvidia", device_name=nvidia_name, rocm_driver_ok=False)

    amd_name, amd_driver = detect_amd()
    if amd_name:
        return GpuInfo(
            vendor="amd",
            device_name=amd_name,
            driver_version=amd_driver,
            rocm_driver_ok=_rocm_tooling_ok(),
            gfx_target=detect_gfx_target(),
        )

    return GpuInfo(vendor="cpu")


def amd_driver_warning(info: GpuInfo) -> str | None:
    """Warning when the AMD/ROCm driver could not be confirmed.

    Returns None for non-AMD vendors or when ROCm tooling is on the PATH.
    """
    if info.vendor != "amd" or info.rocm_driver_ok:
        return None
    return _(
        "[VoiceMate] ⚠ Could not confirm the AMD/ROCm driver. For the GPU to "
        "accelerate, install the Adrenalin driver >= {version} ({url}) "
        "and, on Linux/WSL2, ROCm (rocminfo should list the GPU). "
        "Installation continues regardless — if the GPU is not used, the app falls back to CPU."
    ).format(version=REQUIRED_AMD_DRIVER, url=_AMD_DRIVER_URL)
