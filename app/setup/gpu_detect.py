"""Detect the GPU vendor (NVIDIA / AMD / CPU) without heavy dependencies.

Windows-first (the project's primary platform) but degrades gracefully on any
OS: every external call is wrapped so a missing tool / timeout / error just
falls through to the next check, ending at the always-safe "cpu" default.

Only stdlib is used here — this module is imported at install time, before
torch exists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass

from app.core.config import GpuVendor

# Versão mínima do driver Adrenalin para o ROCm-on-Windows (preview 2025+).
# Só usada em mensagem de aviso — a checagem real é frágil (ver detect_amd).
REQUIRED_AMD_DRIVER = "26.2.2"

_AMD_DRIVER_URL = "https://www.amd.com/en/support"

_SUBPROCESS_TIMEOUT = 5.0


@dataclass
class GpuInfo:
    """Resultado da detecção de GPU."""

    vendor: GpuVendor
    device_name: str | None = None
    driver_version: str | None = None
    # True quando há tooling HIP/ROCm no PATH; None/False = não dá p/ afirmar,
    # então apenas avisamos (nunca bloqueamos a instalação por isso).
    rocm_driver_ok: bool = False


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
    """Retorna (nome, driver_version) da GPU AMD no Windows, ou (None, None).

    Usa WMI via PowerShell. ATENÇÃO: `DriverVersion` do WMI é a versão do .inf
    (ex. 32.0.x), NÃO a versão Adrenalin (ex. 26.2.2). Serve só para exibição;
    não dá p/ gatear o ROCm por ela.
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
        rocm_ok = shutil.which("hipInfo") is not None or shutil.which("hipconfig") is not None
        return GpuInfo(
            vendor="amd",
            device_name=amd_name,
            driver_version=amd_driver,
            rocm_driver_ok=rocm_ok,
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
        f"acelerar, instale o driver Adrenalin >= {REQUIRED_AMD_DRIVER} ({_AMD_DRIVER_URL}). "
        f"A instalação segue mesmo assim — se a GPU não for usada, o app cai para CPU."
    )
