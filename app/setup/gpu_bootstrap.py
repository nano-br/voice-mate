"""Interactive GPU bootstrap — detect the GPU, confirm with the user, install
the matching torch + feature extras, and remember the choice.

Invoked by ``make setup`` (first run) and ``make configure`` (re-pick):

    poetry run python -m app.setup.gpu_bootstrap [--reconfigure]
                                                 [--vendor {nvidia,amd,cpu}]
                                                 [--yes] [--extras "..."]

Only stdlib + the light `app.setup` modules are imported here — never
`torch`/`voxcpm` (they may not be installed yet when this runs). Mensagens em
PT-BR cru, como o resto do código operacional (wiring/config_builder).
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import cast

from app.core.config import FlowKind, GpuVendor, WhisperBackend
from app.core.console import force_utf8_stdio
from app.setup.gpu_detect import amd_driver_warning, detect_gpu
from app.setup.persisted_config import PersistedConfig, load_persisted, save_persisted

# Wheels ROCm-on-Windows oficiais da AMD (não estão no PyPI). Verificar/atualizar
# para a última release em https://repo.radeon.com/rocm/windows/ ao manter.
_ROCM_REL = "rocm-rel-7.2.1"
_ROCM_BASE = f"https://repo.radeon.com/rocm/windows/{_ROCM_REL}"
# Passo 1: runtime ROCm SDK. O torch ROCm DEPENDE dele só para importar — sem
# ele, `import torch` quebra com ModuleNotFoundError: rocm_sdk. (Comando oficial
# da AMD; torchvision é omitido de propósito — o projeto não usa.)
_ROCM_SDK_WHEELS = [
    f"{_ROCM_BASE}/rocm_sdk_core-7.2.1-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm_sdk_devel-7.2.1-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm_sdk_libraries_custom-7.2.1-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm-7.2.1.tar.gz",
]
# Passo 2: torch + torchaudio ROCm.
_ROCM_WHEELS = [
    f"{_ROCM_BASE}/torch-2.9.1+rocm7.2.1-cp312-cp312-win_amd64.whl",
    f"{_ROCM_BASE}/torchaudio-2.9.1+rocm7.2.1-cp312-cp312-win_amd64.whl",
]
_TORCH_INDEX: dict[str, str] = {
    "nvidia": "https://download.pytorch.org/whl/cu128",
    "cpu": "https://download.pytorch.org/whl/cpu",
}

# whisper.cpp + Vulkan (backend de transcrição preferido na AMD). Binário nativo
# (não-pip) + modelo GGUF, baixados com SHA-256 fixado p/ integridade. O modelo
# default é o turbo fp16 (qualidade de referência, ~1,6 GB de VRAM).
_WHISPERCPP_DIR = Path.home() / ".cache" / "voicemate" / "whispercpp"
_WCPP_BIN_URL = (
    "https://github.com/jerryshell/whisper.cpp-windows-vulkan-bin/"
    "releases/download/v1.0.0/whisper.cpp-windows-vulkan.zip"
)
_WCPP_BIN_SHA256 = "a5d408c72e460433b39875f74a0b6e27e60a3724301d478fe9873db7ff4098e0"
_WCPP_MODEL_NAME = "ggml-large-v3-turbo.bin"
_WCPP_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{_WCPP_MODEL_NAME}?download=true"
_WCPP_MODEL_SHA256 = "1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69"
# Do zip só guardamos o necessário: os CLIs + as DLLs (Vulkan/ggml/whisper).
_WCPP_KEEP_EXACT = ("whisper-cli.exe", "whisper-server.exe")


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = _parse_args(argv)
    interactive = not args.yes

    saved = load_persisted()
    info = detect_gpu()
    name = f" — {info.device_name}" if info.device_name else ""
    print(f"[setup] GPU detectada: {info.vendor.upper()}{name}")
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
    if flow == "claude_chat":
        default_tts = saved.tts_enabled if saved.tts_enabled is not None else True
        tts_enabled = _prompt_yes_no("Habilitar TTS (resposta falada do Claude)?", default_tts, interactive)
    else:
        tts_enabled = False

    backend: WhisperBackend = "whispercpp" if vendor == "amd" else "faster-whisper"
    extras = set(args.extras.split()) if args.extras is not None else _compute_extras(flow, tts_enabled)

    print(
        f"\n[setup] Plano: vendor={vendor}, transcrição={backend}, fluxo={flow}, "
        f"tts={tts_enabled}, extras=[{' '.join(sorted(extras)) or '—'}]"
    )

    _poetry_install_extras(extras)
    _install_torch(vendor)
    _verify_torch(vendor)
    if backend == "whispercpp":
        _install_whispercpp()

    if _prompt_yes_no("\nSalvar essas escolhas para a próxima execução?", True, interactive):
        save_persisted(
            PersistedConfig(
                gpu_vendor=vendor,
                whisper_backend=backend,
                tts_enabled=tts_enabled,
                default_flow=flow,
            )
        )
        print("[setup] ✓ Escolhas salvas em ~/.config/voicemate/config.toml")
    else:
        print("[setup] Escolhas não salvas (valem só para esta instalação).")

    print("\n[setup] Pronto! Rode:  make run")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gpu_bootstrap",
        description="Detecta a GPU, instala o torch certo (CUDA/ROCm/CPU) e lembra a escolha.",
    )
    parser.add_argument("--vendor", choices=["nvidia", "amd", "cpu"], default=None, help="Pula a detecção/pergunta.")
    parser.add_argument("--yes", action="store_true", help="Não interativo: aceita os defaults (detectado/salvo).")
    parser.add_argument(
        "--reconfigure", action="store_true", help="Re-pergunta as escolhas (usado por make configure)."
    )
    parser.add_argument("--extras", default=None, help='Sobrescreve os extras do poetry (ex.: "all").')
    return parser.parse_args(argv)


def _compute_extras(flow: FlowKind, tts_enabled: bool) -> set[str]:
    # AMD usa whisper.cpp (binário, não-pip) p/ transcrição — sem extra openai-whisper.
    # O torch ROCm (instalado à parte) cobre o VoxCPM/TTS.
    extras: set[str] = set()
    if flow == "claude_chat":
        extras.add("claude")
        if tts_enabled:
            extras.add("tts")
    return extras


def _prompt_vendor(default: GpuVendor, interactive: bool) -> GpuVendor:
    if not interactive:
        return default
    labels: dict[GpuVendor, str] = {
        "nvidia": "NVIDIA (CUDA — faster-whisper na GPU)",
        "amd": "AMD (ROCm — VoxCPM na GPU + openai-whisper)",
        "cpu": "CPU (sem GPU)",
    }
    order: list[GpuVendor] = ["nvidia", "amd", "cpu"]
    print("\nQual configuração instalar?")
    for i, vendor in enumerate(order, 1):
        mark = "  (detectada/sugerida)" if vendor == default else ""
        print(f"  {i}) {labels[vendor]}{mark}")
    raw = _ask(f"Escolha [1-3] (Enter = {default}): ").lower()
    mapping: dict[str, GpuVendor] = {
        "1": "nvidia",
        "2": "amd",
        "3": "cpu",
        "nvidia": "nvidia",
        "amd": "amd",
        "cpu": "cpu",
    }
    return mapping.get(raw, default)


def _prompt_flow(interactive: bool, default: FlowKind) -> FlowKind:
    if not interactive:
        return default
    print("\nQual fluxo principal?")
    print("  1) clipboard — voz vira texto no clipboard (só transcrição)")
    print("  2) claude_chat — voz → Claude → resposta falada (+ clipboard)")
    raw = _ask(f"Escolha [1-2] (Enter = {default}): ").lower()
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
    suffix = "[S/n]" if default_yes else "[s/N]"
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


def _install_torch(vendor: GpuVendor) -> bool:
    pip = [sys.executable, "-m", "pip", "install"]
    if vendor == "amd":
        # 1) runtime ROCm SDK (sem ele o torch ROCm nem importa).
        ok_sdk = _run(pip + ["--no-cache-dir", *_ROCM_SDK_WHEELS], "Instalando ROCm SDK (runtime AMD)...")
        # 2) torch+torchaudio ROCm: --force-reinstall vence o torch CPU que o
        #    poetry puxou; --no-deps evita que o pip o troque por um CPU do PyPI.
        ok_torch = _run(
            pip + ["--no-cache-dir", "--force-reinstall", "--no-deps", *_ROCM_WHEELS],
            "Instalando torch ROCm (AMD)...",
        )
        return ok_sdk and ok_torch
    index = _TORCH_INDEX[vendor]
    label = "Instalando torch CUDA (NVIDIA)..." if vendor == "nvidia" else "Instalando torch CPU..."
    return _run(pip + ["--force-reinstall", "--index-url", index, "torch", "torchaudio"], label)


def _poetry_install_extras(extras: set[str]) -> bool:
    cmd = ["poetry", "install"]
    if extras:
        cmd += ["--extras", " ".join(sorted(extras))]
    return _run(cmd, "Instalando dependências (poetry)...")


def _verify_torch(vendor: GpuVendor) -> None:
    code = "import torch; print('torch', torch.__version__); print('cuda', torch.cuda.is_available())"
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[setup] ⚠ Não foi possível verificar o torch: {exc}", file=sys.stderr)
        return
    out = (proc.stdout or "").strip()
    if out:
        print("[setup] Verificação do torch:")
        print("  " + out.replace("\n", "\n  "))
    accelerated = "cuda true" in out.lower()
    if vendor in ("nvidia", "amd") and not accelerated:
        print(
            "[setup] ⚠ A GPU não foi reconhecida pelo torch — o app cairá para CPU. "
            "Verifique o driver e rode `make configure`.",
            file=sys.stderr,
        )
    elif accelerated:
        print("[setup] ✓ GPU pronta para o torch.")


def _install_whispercpp() -> bool:
    """Baixa o whisper.cpp + Vulkan (binário) e o modelo GGUF, com SHA-256 fixado."""
    _WHISPERCPP_DIR.mkdir(parents=True, exist_ok=True)
    exe = _WHISPERCPP_DIR / "whisper-cli.exe"
    model = _WHISPERCPP_DIR / _WCPP_MODEL_NAME
    if exe.exists() and model.exists():
        print(f"[setup] whisper.cpp já presente em {_WHISPERCPP_DIR}.")
        return True
    print("[setup] Instalando whisper.cpp + Vulkan (binário ~17 MB + modelo turbo fp16 ~1,5 GB)...")
    if not exe.exists():
        zip_path = _WHISPERCPP_DIR / "_whispercpp.zip"
        if not _download_verified(_WCPP_BIN_URL, zip_path, _WCPP_BIN_SHA256):
            return False
        _extract_whispercpp(zip_path)
        zip_path.unlink(missing_ok=True)
    if not model.exists() and not _download_verified(_WCPP_MODEL_URL, model, _WCPP_MODEL_SHA256):
        return False
    print(f"[setup] ✓ whisper.cpp pronto em {_WHISPERCPP_DIR}")
    return True


def _extract_whispercpp(zip_path: Path) -> None:
    """Extrai só os CLIs + DLLs (basename apenas → sem path traversal)."""
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = Path(info.filename).name
            if name in _WCPP_KEEP_EXACT or name.endswith(".dll"):
                (_WHISPERCPP_DIR / name).write_bytes(archive.read(info))


def _download_verified(url: str, dest: Path, expected_sha256: str) -> bool:
    print(f"[setup] baixando {url}")
    try:
        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        print()
    except OSError as exc:
        print(f"[setup] ⚠ falha no download: {exc}", file=sys.stderr)
        return False
    digest = _sha256_file(dest)
    if digest.lower() != expected_sha256.lower():
        print(f"[setup] ⚠ SHA-256 não confere para {dest.name} (obtido {digest}); removendo.", file=sys.stderr)
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
    if total_size <= 0 or block_num % 256 != 0:  # imprime ~a cada 2 MB
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
        print(f"[setup] ⚠ Falha ao executar: {exc}", file=sys.stderr)
        return False
    if proc.returncode != 0:
        print(f"[setup] ⚠ Comando retornou código {proc.returncode}.", file=sys.stderr)
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
