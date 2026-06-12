"""Detect the GPU vendor (NVIDIA / AMD / CPU) without heavy dependencies.

Cobre Windows (WMI via PowerShell) e Linux/WSL2 (rocm-smi → amd-smi → lspci).
Degrada graciosamente em qualquer SO: every external call is wrapped so a
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

# Versão mínima do driver Adrenalin para o ROCm (Windows e WSL2).
# Só usada em mensagem de aviso — a checagem real é frágil (ver detect_amd).
REQUIRED_AMD_DRIVER = "26.2.2"

_AMD_DRIVER_URL = "https://www.amd.com/en/support"

_SUBPROCESS_TIMEOUT = 5.0

_GFX_RE = re.compile(r"\bgfx[0-9a-f]{3,}\b")


@dataclass
class GpuInfo:
    """Resultado da detecção de GPU."""

    vendor: GpuVendor
    device_name: str | None = None
    driver_version: str | None = None
    # True quando há tooling HIP/ROCm utilizável; None/False = não dá p/ afirmar,
    # então apenas avisamos (nunca bloqueamos a instalação por isso).
    rocm_driver_ok: bool = False
    # Arquitetura da GPU AMD (ex.: "gfx1201" na RX 9070 XT) via rocminfo —
    # usada como GPU_TARGETS no build do CTranslate2-ROCm.
    gfx_target: str | None = None


def _run(cmd: list[str]) -> str | None:
    """Roda um comando e devolve stdout (strip), ou None em qualquer falha."""
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
    """Retorna o nome da GPU NVIDIA via `nvidia-smi`, ou None."""
    if shutil.which("nvidia-smi") is None:
        return None
    out = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if not out:
        return None
    # Primeira linha = primeira GPU.
    return out.splitlines()[0].strip()


def detect_amd() -> tuple[str | None, str | None]:
    """Retorna (nome, driver_version) da GPU AMD, ou (None, None)."""
    if sys.platform == "win32":
        return _detect_amd_windows()
    return _detect_amd_linux()


def _detect_amd_windows() -> tuple[str | None, str | None]:
    """AMD no Windows via WMI/PowerShell.

    ATENÇÃO: `DriverVersion` do WMI é a versão do .inf (ex. 32.0.x), NÃO a
    versão Adrenalin (ex. 26.2.2). Serve só para exibição.
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
    """AMD no Linux/WSL2: rocminfo → rocm-smi → amd-smi → lspci.

    O rocminfo vem primeiro de propósito: é a única das ferramentas que funciona
    TAMBÉM no WSL2 (rocm-smi/amd-smi não operam lá por limitação de arquitetura),
    e o "Marketing Name" dele traz o nome comercial exato (ex.: "AMD Radeon
    RX 9070 XT") — o lspci, último fallback, mostra só o nome do chip.
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
    """Nome comercial da GPU na saída do `rocminfo`.

    O rocminfo lista agentes CPU e GPU, ambos com "Marketing Name"; filtramos
    pelo que parece placa de vídeo (Radeon/Instinct/Graphics) para não devolver
    o nome do Ryzen.
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
    """Extrai o nome do card da saída do `rocm-smi --showproductname`."""
    for line in out.splitlines():
        if "card series" in line.lower() or "card model" in line.lower():
            value = line.split(":")[-1].strip()
            if value:
                return value
    return None


def _parse_amd_smi_asic(out: str) -> str | None:
    """Extrai MARKET_NAME da saída do `amd-smi static --asic`."""
    for line in out.splitlines():
        if "market_name" in line.lower():
            value = line.split(":")[-1].strip()
            if value:
                return value
    return None


_LSPCI_AMD_RE = re.compile(r"\b(amd|ati|radeon)\b", re.IGNORECASE)


def _parse_lspci_amd(out: str) -> str | None:
    """Acha a linha VGA/Display da AMD no `lspci` e devolve a descrição.

    Word boundary obrigatório: "Corporation" contém "ati" — sem \\b a NVIDIA
    passaria no filtro.
    """
    for line in out.splitlines():
        low = line.lower()
        if ("vga" in low or "display" in low or "3d controller" in low) and _LSPCI_AMD_RE.search(line):
            # Formato: "03:00.0 VGA compatible controller: <descrição>"
            return line.split(":", 2)[-1].strip()
    return None


def _amdgpu_kernel_version() -> str | None:
    try:
        return Path("/sys/module/amdgpu/version").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def detect_gfx_target() -> str | None:
    """Arquitetura gfx da GPU AMD via `rocminfo` (ex.: gfx1201). None se indisponível."""
    if shutil.which("rocminfo") is None:
        return None
    out = _run(["rocminfo"])
    if not out:
        return None
    return _parse_gfx_target(out)


def _parse_gfx_target(out: str) -> str | None:
    """Primeiro alvo gfx* da saída do rocminfo (a GPU; CPUs não têm gfx)."""
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
    """Detecta o vendor da GPU. Ordem: NVIDIA → AMD → CPU (fallback seguro)."""
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
    """Aviso quando o driver AMD/ROCm não pôde ser confirmado.

    Retorna None p/ vendors não-AMD ou quando há tooling ROCm no PATH.
    """
    if info.vendor != "amd" or info.rocm_driver_ok:
        return None
    return (
        f"[VoiceMate] ⚠ Não foi possível confirmar o driver AMD/ROCm. Para a GPU "
        f"acelerar, instale o driver Adrenalin >= {REQUIRED_AMD_DRIVER} ({_AMD_DRIVER_URL}) "
        f"e, no Linux/WSL2, o ROCm (rocminfo deve listar a GPU). "
        f"A instalação segue mesmo assim — se a GPU não for usada, o app cai para CPU."
    )
