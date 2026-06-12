"""Build + instalação do fork ROCm do CTranslate2 (faster-whisper na GPU AMD).

O CTranslate2 oficial não tem backend ROCm (e trava na gfx1201); o fork
`arlo-phoenix/CTranslate2-rocm` compila o backend "CUDA" via HIP, e o
faster-whisper passa a acelerar na AMD com `device="cuda"` — qualidade
idêntica à da main em NVIDIA.

Build from source (demora bastante — dezenas de minutos a horas). Tudo aqui é
best-effort: qualquer falha imprime o motivo + a correção e retorna False, e o
chamador (gpu_bootstrap) persiste `ct2_rocm_ok=false` e segue para o
whisper.cpp SEM abortar o setup. `make configure` re-tenta.

Só stdlib — roda em install time.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
    """Clona, builda, instala o wrapper python e valida. False em qualquer falha."""
    missing = _missing_tools()
    if missing:
        _log(f"⚠ Ferramentas ausentes p/ o build do CT2-ROCm: {', '.join(missing)}")
        _log(f"  Instale com: {_APT_HINT}")
        _log("  (no WSL2, instale também o ROCm: https://rocm.docs.amd.com)")
        return False
    if gfx_target is None:
        _log("⚠ gfx target da GPU não detectado (rocminfo); usando lista default do fork.")

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not _clone():
        return False
    if not _build(gfx_target):
        return False
    if not _pip_install_wrapper():
        return False
    if not verify():
        _log("⚠ Build concluiu mas a validação falhou (import/GPU). Veja mensagens acima.")
        return False

    _MARKER_PATH.write_text(
        json.dumps({"gfx_target": gfx_target, "prefix": str(_PREFIX_DIR)}, indent=2) + "\n",
        encoding="utf-8",
    )
    _log("✓ CTranslate2-ROCm instalado e validado (faster-whisper acelera na GPU AMD).")
    return True


def verify() -> bool:
    """Valida em subprocess: import + ao menos 1 device 'cuda' (HIP) visível."""
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
        _log(f"⚠ Validação do CT2-ROCm não rodou: {exc}")
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
    """Env com LD_LIBRARY_PATH apontando p/ a libctranslate2 instalada no prefix."""
    env = dict(os.environ)
    lib_dir = str(_PREFIX_DIR / "lib")
    current = env.get("LD_LIBRARY_PATH", "")
    if lib_dir not in current.split(":"):
        env["LD_LIBRARY_PATH"] = f"{lib_dir}:{current}" if current else lib_dir
    # Mesmo workaround do runtime (wiring): allocator default do CT2 dá
    # "Memory access fault" em gfx1201 — validar já com o allocator certo.
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
        _log(f"Fonte já clonado em {_SRC_DIR} (reusando).")
        return True
    return _run(
        ["git", "clone", "--recursive", "--depth", "1", _REPO_URL, str(_SRC_DIR)],
        "Clonando CTranslate2-rocm (com submódulos)...",
    )


def _build(gfx_target: str | None) -> bool:
    env = dict(os.environ)
    if gfx_target:
        # O build HIP usa GPU_TARGETS/CMAKE_HIP_ARCHITECTURES p/ compilar kernels
        # só para a arquitetura local (build mais rápido e menor).
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
        # rpath embutido: o wrapper python acha a lib sem LD_LIBRARY_PATH global.
        f"-DCMAKE_INSTALL_RPATH={_PREFIX_DIR / 'lib'}",
    ]
    if gfx_target:
        configure.append(f"-DCMAKE_HIP_ARCHITECTURES={gfx_target}")
    if not _run(configure, "Configurando build (cmake)...", env=env):
        return False
    jobs = str(max(1, (os.cpu_count() or 4) - 1))
    if not _run(
        ["cmake", "--build", str(_BUILD_DIR), "--parallel", jobs],
        f"Compilando (paralelo={jobs}; pode demorar MUITO — café ☕)...",
        env=env,
    ):
        return False
    return _run(
        ["cmake", "--install", str(_BUILD_DIR)],
        f"Instalando em {_PREFIX_DIR}...",
        env=env,
    )


def _pip_install_wrapper() -> bool:
    env = dict(os.environ)
    env["CTRANSLATE2_ROOT"] = str(_PREFIX_DIR)
    # rpath no módulo nativo do wrapper → import funciona sem LD_LIBRARY_PATH.
    env["LDFLAGS"] = f"-Wl,-rpath,{_PREFIX_DIR / 'lib'} " + env.get("LDFLAGS", "")
    return _run(
        [sys.executable, "-m", "pip", "install", "--force-reinstall", str(_SRC_DIR / "python")],
        "Instalando o wrapper python do CTranslate2-ROCm no venv...",
        env=env,
    )


def _run(cmd: list[str], desc: str, env: dict[str, str] | None = None) -> bool:
    _log(desc)
    _log("$ " + " ".join(cmd))
    try:
        proc = subprocess.run(cmd, env=env)
    except OSError as exc:
        _log(f"⚠ Falha ao executar: {exc}")
        return False
    if proc.returncode != 0:
        _log(f"⚠ Comando retornou código {proc.returncode}.")
        return False
    return True


def _log(message: str) -> None:
    print(f"[setup:ct2-rocm] {message}", flush=True)
