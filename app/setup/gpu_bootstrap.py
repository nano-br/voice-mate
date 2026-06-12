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
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import cast

from app.core.config import FlowKind, GpuVendor, WhisperBackend
from app.core.console import force_utf8_stdio
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind
from app.setup.gpu_detect import GpuInfo, amd_driver_warning, detect_gpu
from app.setup.persisted_config import PersistedConfig, load_persisted, save_persisted

# ── Releases ROCm (uma constante por trilha; as trilhas têm versões PRÓPRIAS) ──
# Windows (ROCm-on-Windows): wheels em https://repo.radeon.com/rocm/windows/.
_ROCM_WIN_VER = "7.2.1"
# Linux/WSL2: a LINHA é "7.2" (pacote WSL 7.2.70200) e os wheels manylinux são
# "+rocm7.2.0" — em https://repo.radeon.com/rocm/manylinux/rocm-rel-7.2/.
# (Não confundir com a trilha "7.2.4", que é Linux nativo e não tem usecase wsl.)
_ROCM_LINUX_LINE = "7.2"

# Wheels ROCm-on-Windows oficiais da AMD (não estão no PyPI). Verificar/atualizar
# para a última release em https://repo.radeon.com/rocm/windows/ ao manter.
_ROCM_REL = f"rocm-rel-{_ROCM_WIN_VER}"
_ROCM_BASE = f"https://repo.radeon.com/rocm/windows/{_ROCM_REL}"
# Passo 1: runtime ROCm SDK. O torch ROCm DEPENDE dele só para importar — sem
# ele, `import torch` quebra com ModuleNotFoundError: rocm_sdk. (Comando oficial
# da AMD; torchvision é omitido de propósito — o projeto não usa.)
_ROCM_SDK_WHEELS = [
    f"{_ROCM_BASE}/rocm_sdk_core-{_ROCM_WIN_VER}-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm_sdk_devel-{_ROCM_WIN_VER}-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm_sdk_libraries_custom-{_ROCM_WIN_VER}-py3-none-win_amd64.whl",
    f"{_ROCM_BASE}/rocm-{_ROCM_WIN_VER}.tar.gz",
]
# Passo 2: torch + torchaudio ROCm.
_ROCM_WHEELS = [
    f"{_ROCM_BASE}/torch-2.9.1+rocm{_ROCM_WIN_VER}-cp312-cp312-win_amd64.whl",
    f"{_ROCM_BASE}/torchaudio-2.9.1+rocm{_ROCM_WIN_VER}-cp312-cp312-win_amd64.whl",
]
_TORCH_INDEX: dict[str, str] = {
    "nvidia": "https://download.pytorch.org/whl/cu128",
    "cpu": "https://download.pytorch.org/whl/cpu",
}
# Linux/WSL2 + AMD: wheels manylinux que a AMD publica e TESTA para WSL
# (torch+torchvision+torchaudio+triton casados com o ROCm da linha). Preferidos
# ao índice do pytorch.org porque são a combinação validada pela AMD — e o
# triton (TunableOp/flash-attn) só vem por aqui. Exigem numpy < 2.0 e cp312.
_ROCM_LINUX_BASE = f"https://repo.radeon.com/rocm/manylinux/rocm-rel-{_ROCM_LINUX_LINE}"
_ROCM_LINUX_NUMPY_PIN = "numpy==1.26.4"
_ROCM_LINUX_WHEELS = [
    f"{_ROCM_LINUX_BASE}/torch-2.9.1%2Brocm7.2.0.lw.git7e1940d4-cp312-cp312-linux_x86_64.whl",
    f"{_ROCM_LINUX_BASE}/torchvision-0.24.0%2Brocm7.2.0.gitb919bd0c-cp312-cp312-linux_x86_64.whl",
    f"{_ROCM_LINUX_BASE}/torchaudio-2.9.0%2Brocm7.2.0.gite3c6ee2b-cp312-cp312-linux_x86_64.whl",
    f"{_ROCM_LINUX_BASE}/triton-3.5.1%2Brocm7.2.0.gita272dfa8-cp312-cp312-linux_x86_64.whl",
]

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

# Linux: build from source (binário oficial Linux+Vulkan não é distribuído).
# Tag pinada p/ reprodutibilidade; binários estáticos (BUILD_SHARED_LIBS=OFF).
_WCPP_LINUX_REPO = "https://github.com/ggml-org/whisper.cpp"
_WCPP_LINUX_TAG = "v1.8.6"
# Nota: spirv-headers/spirv-tools/glslang-tools são necessários p/ compilar os
# shaders Vulkan (sem eles o cmake falha em SPIRV-Headers/glslangValidator).
# `libglslang-dev` NÃO existe com esse nome no Ubuntu 24.04 — não sugerir.
_WCPP_LINUX_APT_HINT = (
    "sudo apt install -y git cmake build-essential libvulkan-dev glslc vulkan-tools "
    "spirv-headers spirv-tools glslang-tools"
)

# Modelo Q8_0 (near-lossless, ~0,9 GB vs ~1,6 GB do fp16) — opção p/ VRAM curta.
_WCPP_MODEL_Q8_NAME = "ggml-large-v3-turbo-q8_0.bin"
_WCPP_MODEL_Q8_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{_WCPP_MODEL_Q8_NAME}?download=true"
_WCPP_MODEL_Q8_SHA256 = "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1"

# silero-VAD em GGML (corte por silêncio — evita cortar palavras no meio).
# Só baixado no Linux: o binário pinado do Windows é antigo e não tem --vad.
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
    print(f"[setup] Plataforma: {platform} (gatilho default: {trigger})")
    info = detect_gpu()
    name = f" — {info.device_name}" if info.device_name else ""
    gfx = f" [{info.gfx_target}]" if info.gfx_target else ""
    print(f"[setup] GPU detectada: {info.vendor.upper()}{name}{gfx}")
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

    # Linux nativo: hotkeys precisam de pynput/evdev (extra linux). WSL2 usa o
    # daemon HTTP (sem dependência extra).
    if platform in ("linux-x11", "linux-wayland"):
        extras.add("linux")

    print(
        f"\n[setup] Plano: plataforma={platform}, vendor={vendor}, transcrição={backend}, "
        f"fluxo={flow}, tts={tts_enabled}, extras=[{' '.join(sorted(extras)) or '—'}]"
    )

    _poetry_install_extras(extras)
    torch_ok = _install_torch(vendor, platform)
    _verify_torch(vendor)
    if not torch_ok and vendor != "cpu":
        print(
            "[setup] ⚠ A instalação do torch da GPU falhou (veja o erro acima). "
            "Sem ela o app cai para CPU. Corrija e rode `make configure`.",
            file=sys.stderr,
        )
    if backend == "whispercpp":
        if platform == "windows":
            _install_whispercpp()
        else:
            _install_whispercpp_linux(interactive)

    # CTranslate2-ROCm (faster-whisper na GPU AMD — qualidade idêntica à main).
    # Build pesado e opt-in; falha NÃO aborta (cadeia cai p/ whisper.cpp).
    ct2_rocm_ok: bool | None = saved.ct2_rocm_ok
    if vendor == "amd" and platform != "windows":
        ct2_rocm_ok = _maybe_install_ct2_rocm(info, saved, interactive)

    if _prompt_yes_no("\nSalvar essas escolhas para a próxima execução?", True, interactive):
        save_persisted(
            PersistedConfig(
                gpu_vendor=vendor,
                whisper_backend=backend,
                tts_enabled=tts_enabled,
                default_flow=flow,
                platform=platform,
                trigger=trigger,
                stt_strategy=saved.stt_strategy or "auto",
                ct2_rocm_ok=ct2_rocm_ok,
                daemon_port=saved.daemon_port,
            )
        )
        print("[setup] ✓ Escolhas salvas em ~/.config/voicemate/config.toml")
    else:
        print("[setup] Escolhas não salvas (valem só para esta instalação).")

    if platform == "wsl2":
        _maybe_install_systemd_unit(interactive)

    print("\n[setup] Pronto! Rode:  make run")
    print("[setup] Diagnóstico do ambiente (áudio/mic/hotkeys):  make doctor")
    if platform == "wsl2":
        print("[setup] WSL2: registre as hotkeys do lado Windows — veja docs/wsl2.md.")
    return 0


def _maybe_install_systemd_unit(interactive: bool) -> None:
    """Instala o user service do systemd (autostart do daemon no WSL2). Opt-in."""
    template = Path(__file__).resolve().parents[2] / "scripts" / "systemd" / "voicemate.service"
    if not template.exists():
        return
    if not _prompt_yes_no("\nInstalar o serviço systemd (daemon inicia junto com o WSL)?", False, interactive):
        print("[setup] Serviço systemd não instalado (instruções em docs/wsl2.md).")
        return
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    content = template.read_text(encoding="utf-8").replace(
        "WorkingDirectory=%h/voice-mate", f"WorkingDirectory={repo_root}"
    )
    (unit_dir / "voicemate.service").write_text(content, encoding="utf-8")
    ok = _run(["systemctl", "--user", "daemon-reload"], "Recarregando units do systemd (user)...")
    if ok:
        _run(["systemctl", "--user", "enable", "--now", "voicemate"], "Habilitando o serviço voicemate...")
        print("[setup] ✓ Serviço instalado. Logs: journalctl --user -u voicemate -f")
        print("[setup]   Para sobreviver sem sessão aberta: loginctl enable-linger $USER")


def _maybe_install_ct2_rocm(info: GpuInfo, saved: PersistedConfig, interactive: bool) -> bool | None:
    from app.setup import ct2_rocm

    if saved.ct2_rocm_ok is True and ct2_rocm.is_installed():
        print("[setup] CTranslate2-ROCm já instalado e validado (reusando).")
        return True
    default_try = saved.ct2_rocm_ok is not False  # já falhou antes → default Não
    wants = _prompt_yes_no(
        "\nInstalar o CTranslate2-ROCm? (faster-whisper na GPU AMD — qualidade máxima;\n"
        "build from source DEMORADO. Se pular ou falhar, o whisper.cpp assume.)",
        default_try,
        interactive,
    )
    if not wants:
        print("[setup] CT2-ROCm pulado (whisper.cpp será o backend de transcrição).")
        return saved.ct2_rocm_ok
    ok = ct2_rocm.install(info.gfx_target)
    if not ok:
        print(
            "[setup] ⚠ CT2-ROCm falhou — seguindo com whisper.cpp (sem abortar). "
            "Corrija os pré-requisitos e rode `make configure` para re-tentar.",
            file=sys.stderr,
        )
    return ok


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


def _linux_rocm_torch_already_ok() -> bool:
    """True se o venv já tem um torch +rocm acelerando — não sobrescrever.

    Protege uma instalação validada manualmente (ex.: wheels do repo.radeon.com)
    de um --force-reinstall que trocaria uma build testada por outra.
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
    """Remove a libhsa-runtime64 embutida no torch (passo oficial AMD p/ WSL).

    No WSL o runtime HSA correto vem do sistema (usecase wsl); a cópia dentro do
    wheel pode sombreá-lo. Em releases recentes o arquivo nem existe — ausência
    é OK, o passo é mantido por segurança para outras versões.
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
            print(f"[setup] Removido {lib.name} do torch (o runtime HSA do WSL é o do sistema).")
        except OSError:
            pass


def _install_torch(vendor: GpuVendor, platform: PlatformKind) -> bool:
    pip = [sys.executable, "-m", "pip", "install"]
    if vendor == "amd" and platform != "windows":
        # Linux/WSL2: wheels manylinux do repo.radeon.com (a combinação que a
        # AMD testa p/ WSL: torch+torchvision+torchaudio+triton + numpy<2).
        if sys.version_info[:2] != (3, 12):
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            print(
                f"[setup] ⚠ Os wheels ROCm Linux são cp312 (este Python é {ver}). "
                "Recrie o ambiente com 3.12 (`poetry env use 3.12`) e rode `make configure`.",
                file=sys.stderr,
            )
            return False
        if _linux_rocm_torch_already_ok():
            print("[setup] ✓ torch ROCm já presente e acelerando — mantendo a instalação validada.")
            print("[setup]   (para forçar reinstalação: pip uninstall torch e rode `make configure`)")
            return True
        ok = _run(
            pip + ["--force-reinstall", _ROCM_LINUX_NUMPY_PIN, *_ROCM_LINUX_WHEELS],
            "Instalando torch ROCm (wheels oficiais da AMD p/ Linux/WSL2)...",
        )
        if ok:
            _cleanup_libhsa()
        return ok
    if vendor == "amd":
        # Os wheels ROCm da AMD são cp312-only — num Python diferente o pip
        # rejeita ("not a supported wheel on this platform") e sobraria o torch
        # CPU. Falha cedo e claro em vez de instalar GPU pela metade.
        if sys.version_info[:2] != (3, 12):
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            print(
                f"[setup] ⚠ As wheels ROCm exigem Python 3.12 (este é {ver}). "
                "Recrie o ambiente com 3.12 (`poetry env use 3.12`) e rode `make configure`.",
                file=sys.stderr,
            )
            return False
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


def _install_whispercpp_linux(interactive: bool) -> bool:
    """Builda o whisper.cpp (Vulkan, binário estático) e baixa modelo + VAD.

    Vulkan é o backend default no Linux/WSL2 hoje: o HIP p/ gfx120X ainda está
    em PR no whisper.cpp (ggml-org/whisper.cpp#3757). Quando mergear, dá para
    trocar o flag do cmake por -DGGML_HIP=ON.
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
                f"[setup] ⚠ Ferramentas ausentes p/ compilar o whisper.cpp: {', '.join(missing)}",
                file=sys.stderr,
            )
            print(f"[setup]   Instale com: {_WCPP_LINUX_APT_HINT}", file=sys.stderr)
            return False
        binaries_ok = _build_whispercpp_linux()
        if not binaries_ok:
            return False

    ok = True
    if not model.exists():
        # fp16 turbo (qualidade de referência). Q8_0 fica disponível p/ quem
        # quiser economizar VRAM (baixa manualmente; find_model pega qualquer ggml-*).
        ok = _download_verified(_WCPP_MODEL_URL, model, _WCPP_MODEL_SHA256) and ok
    if not vad.exists():
        ok = _download_verified(_WCPP_VAD_URL, vad, _WCPP_VAD_SHA256) and ok
    if ok:
        print(f"[setup] ✓ whisper.cpp (Linux/Vulkan) pronto em {_WHISPERCPP_DIR}")
    return ok


def _build_whispercpp_linux() -> bool:
    src = _WHISPERCPP_DIR / "src"
    build = src / "build"
    if not (src / ".git").exists():
        if not _run(
            ["git", "clone", "--depth", "1", "--branch", _WCPP_LINUX_TAG, _WCPP_LINUX_REPO, str(src)],
            f"Clonando whisper.cpp {_WCPP_LINUX_TAG}...",
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
        "Configurando build do whisper.cpp (cmake + Vulkan)...",
    ):
        return False
    jobs = str(max(1, (os.cpu_count() or 4) - 1))
    if not _run(
        ["cmake", "--build", str(build), "--parallel", jobs],
        "Compilando whisper.cpp (alguns minutos)...",
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
            print(f"[setup] ⚠ Binário não encontrado após o build: {built}", file=sys.stderr)
    return copied == 2


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
