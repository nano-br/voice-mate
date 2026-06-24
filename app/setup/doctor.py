"""Diagnóstico do ambiente (``make doctor``) — verifica, explica, nunca aborta.

Cada checagem imprime ✓/✗ e, em caso de falha, o comando exato de correção.
Pensado principalmente para o WSL2 (mic/áudio via PulseAudio RDP) e Linux
nativo (grupo input p/ evdev), mas roda em qualquer plataforma.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.console import force_utf8_stdio
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind, TriggerKind
from app.setup.persisted_config import load_persisted

_WSLG_PULSE_SOCKET = Path("/mnt/wslg/PulseServer")


@dataclass
class CheckResult:
    label: str
    ok: bool | None  # None = não aplicável/pulado
    detail: str = ""
    fix: str = ""


def run_checks() -> list[CheckResult]:
    persisted = load_persisted()
    platform = persisted.platform or detect_platform()
    trigger: TriggerKind = persisted.trigger or default_trigger(platform)
    results: list[CheckResult] = [CheckResult("Plataforma detectada", True, f"{platform} (gatilho: {trigger})")]
    if platform == "wsl2":
        results += _check_wslg_audio()
        results += _check_wsl_clipboard()
    results += _check_sounddevice()
    results += _check_trigger(platform, trigger)
    results += _check_whispercpp()
    if platform in ("wsl2", "linux-x11", "linux-wayland"):
        results += _check_vulkan_gpu(platform)
    results += _check_claude()
    results += _check_kokoro_espeak()
    results += _check_torch_gpu()
    return results


def _check_kokoro_espeak() -> list[CheckResult]:
    """Kokoro precisa do espeak-ng (G2P do PT-BR).

    Heurística: só checa se o pacote `kokoro` estiver instalado (o engine é
    escolha de runtime via --tts-engine, não persistida — instalar o extra
    sinaliza intenção de usar). Sem espeak-ng, o load do Kokoro falha no PT-BR.
    """
    from app.features import tts as tts_feature

    if not tts_feature.is_available("kokoro"):
        return []
    has_espeak = shutil.which("espeak-ng") is not None
    return [
        CheckResult(
            "espeak-ng (Kokoro PT-BR)",
            has_espeak,
            "encontrado" if has_espeak else "ausente — Kokoro PT-BR falha sem ele",
            fix="sudo apt install -y espeak-ng",
        )
    ]


# ─── Checagens ───────────────────────────────────────────────────────────────


def _check_wslg_audio() -> list[CheckResult]:
    results: list[CheckResult] = []
    pulse = os.environ.get("PULSE_SERVER", "")
    socket_ok = _WSLG_PULSE_SOCKET.exists()
    results.append(
        CheckResult(
            "WSLg PulseServer (socket)",
            socket_ok,
            str(_WSLG_PULSE_SOCKET) if socket_ok else "socket não existe",
            fix="Atualize o WSL (`wsl --update` no Windows) e confira guiApplications no .wslconfig.",
        )
    )
    results.append(
        CheckResult(
            "PULSE_SERVER no ambiente",
            bool(pulse) or socket_ok,
            pulse or "(vazio — o shim ALSA pode resolver, mas é recomendado exportar)",
            fix="echo 'export PULSE_SERVER=unix:/mnt/wslg/PulseServer' >> ~/.bashrc && source ~/.bashrc",
        )
    )
    libasound_pulse = any(
        Path(p).exists()
        for p in (
            "/usr/lib/x86_64-linux-gnu/alsa-lib/libasound_module_pcm_pulse.so",
            "/usr/lib/alsa-lib/libasound_module_pcm_pulse.so",
        )
    )
    results.append(
        CheckResult(
            "Plugin ALSA→Pulse (p/ PortAudio)",
            libasound_pulse,
            fix="sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils",
        )
    )
    return results


def _check_wsl_clipboard() -> list[CheckResult]:
    """Escrita no clipboard no WSL2.

    Só os utilitários NATIVOS (wl-copy/xclip) são confiáveis no WSLg. O clip.exe
    via interop falha quando o binfmt do WSLInterop não está registrado (comum
    com systemd) com "Exec format error" — por isso não conta como ✓ sozinho.
    """
    native = [t for t in ("wl-copy", "xclip", "xsel") if shutil.which(t)]
    binfmt_ok = Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    if native:
        detail = ", ".join(native)
    elif binfmt_ok:
        detail = "só clip.exe via interop (fallback incerto)"
    else:
        detail = "nenhum (clip.exe falha sem o binfmt WSLInterop)"
    results = [
        CheckResult(
            "Clipboard nativo (WSLg)",
            bool(native),
            detail,
            fix="sudo apt install -y wl-clipboard   # wl-copy funciona via WSLg e sincroniza com o Windows",
        )
    ]
    # Interop do Windows: quando o WSLInterop não está registrado (systemd limpa o
    # binfmt), o clip.exe não roda — então o ÚNICO caminho de clipboard é o wl-copy
    # (pela ponte do WSLg, que pode ficar instável em sessões longas). Restaurar o
    # interop dá um caminho DIRETO ao clipboard do Windows, independente da ponte.
    results.append(
        CheckResult(
            "Interop Windows (clip.exe direto)",
            binfmt_ok,
            "registrado" if binfmt_ok else "WSLInterop ausente — clip.exe não roda",
            fix=(
                "wsl --update (no Windows) resolve na maioria; ou registre: "
                "sudo sh -c 'echo :WSLInterop:M::MZ::/init:PF > /proc/sys/fs/binfmt_misc/register'"
            ),
        )
    )
    return results


def _check_sounddevice() -> list[CheckResult]:
    """Mic (input) e playback (output) visíveis ao PortAudio — em subprocess
    para não poluir/derrubar o doctor se a lib nativa explodir."""
    code = (
        "import sounddevice as sd, json; devs = sd.query_devices(); "
        "ins = [d['name'] for d in devs if d['max_input_channels'] > 0]; "
        "outs = [d['name'] for d in devs if d['max_output_channels'] > 0]; "
        "print(json.dumps({'in': ins[:3], 'out': outs[:3]}))"
    )
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [CheckResult("Áudio (sounddevice)", False, str(exc), fix="poetry install (dependências core)")]
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-200:]
        return [
            CheckResult(
                "Áudio (sounddevice)",
                False,
                tail,
                fix="sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils",
            )
        ]
    import json as _json

    data = _json.loads(proc.stdout.strip() or "{}")
    has_in = bool(data.get("in"))
    has_out = bool(data.get("out"))
    return [
        CheckResult(
            "Microfone (input device)",
            has_in,
            ", ".join(data.get("in", [])) or "nenhum device de entrada",
            fix=(
                "WSL2: habilite o mic do WSLg (wsl --update; `pactl list sources short` deve mostrar RDPSource). "
                "Linux: confira o PulseAudio/PipeWire."
            ),
        ),
        CheckResult(
            "Saída de áudio (output device)",
            has_out,
            ", ".join(data.get("out", [])) or "nenhum device de saída",
            fix="WSL2: `pactl list sinks short` deve mostrar RDPSink (wsl --update se não).",
        ),
    ]


def _check_trigger(platform: PlatformKind, trigger: TriggerKind) -> list[CheckResult]:
    if trigger == "evdev":
        input_dir = Path("/dev/input")
        events = sorted(input_dir.glob("event*")) if input_dir.is_dir() else []
        readable = any(os.access(event, os.R_OK) for event in events)
        return [
            CheckResult(
                "Acesso a /dev/input (evdev)",
                readable,
                f"{len(events)} device(s)" if events else "sem devices event*",
                fix="sudo usermod -aG input $USER  # e re-logue (ou `newgrp input`)",
            )
        ]
    if trigger == "socket":
        return [
            CheckResult(
                "Gatilho via daemon HTTP",
                True,
                "registre as hotkeys no Windows: scripts/windows/voicemate-hotkeys.ahk (ou .ps1)",
            )
        ]
    if trigger == "pynput":
        has_display = bool(os.environ.get("DISPLAY"))
        return [
            CheckResult(
                "Sessão X11 (DISPLAY) p/ pynput",
                has_display,
                os.environ.get("DISPLAY", ""),
                fix="Sem X11? Use --trigger evdev (Wayland) — requer grupo input.",
            )
        ]
    return [CheckResult("Gatilho (hooks do Windows)", True, "libs keyboard/mouse")]


def _parse_vulkan_devices(summary: str) -> list[tuple[str, str]]:
    """Pares (deviceName, deviceType) da saída do `vulkaninfo --summary`."""
    devices: list[tuple[str, str]] = []
    name: str | None = None
    dtype: str | None = None
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith("deviceName"):
            name = stripped.split("=", 1)[-1].strip()
        elif stripped.startswith("deviceType"):
            dtype = stripped.split("=", 1)[-1].strip()
        if name is not None and dtype is not None:
            devices.append((name, dtype))
            name = dtype = None
    return devices


def _check_vulkan_gpu(platform: PlatformKind) -> list[CheckResult]:
    """O Vulkan enxerga uma GPU real? (relevante p/ o backend whisper.cpp).

    No WSL2 o Mesa só expõe llvmpipe (CPU por software) — whisper.cpp "Vulkan"
    ali roda em software e leva minutos por fala. A GPU real no WSL2 só é
    alcançável via ROCm (openai-whisper / CT2-ROCm).
    """
    if shutil.which("vulkaninfo") is None:
        return [CheckResult("Vulkan (GPU p/ whisper.cpp)", None, "vulkaninfo ausente (vulkan-tools)")]
    out = ""
    try:
        proc = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=20)
        out = proc.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return [CheckResult("Vulkan (GPU p/ whisper.cpp)", None, f"não verificado: {exc}")]
    devices = _parse_vulkan_devices(out)
    real_gpus = [name for name, dtype in devices if "CPU" not in dtype.upper() and "llvmpipe" not in name.lower()]
    detail = ", ".join(f"{name} [{dtype}]" for name, dtype in devices) or "nenhum device"
    fix = (
        "No WSL2 isso é esperado: Vulkan não alcança a GPU — use openai-whisper/CT2-ROCm "
        "(make configure). Em Linux nativo: instale o driver Vulkan da GPU (mesa-vulkan-drivers)."
        if platform == "wsl2"
        else "Instale o driver Vulkan da GPU (mesa-vulkan-drivers) ou use openai-whisper."
    )
    return [CheckResult("Vulkan (GPU p/ whisper.cpp)", bool(real_gpus), detail, fix=fix)]


def _check_whispercpp() -> list[CheckResult]:
    from app.core.config import Config
    from app.features import whispercpp

    directory = whispercpp.resolve_dir(Config())
    model = whispercpp.find_model(directory)
    exe = whispercpp.find_server_exe(directory) or whispercpp.find_exe(directory)
    vad = whispercpp.find_vad_model(directory)
    results = [
        CheckResult(
            "whisper.cpp (binário + modelo)",
            exe is not None and model is not None,
            f"{exe.name if exe else '—'} / {model.name if model else '—'} em {directory}",
            fix="make configure  # baixa/builda o whisper.cpp e o modelo",
        )
    ]
    if exe is not None and model is not None:
        results.append(
            CheckResult(
                "VAD (silero) p/ whisper.cpp",
                vad is not None or sys.platform == "win32",
                vad.name if vad else "sem modelo de VAD (ok no Windows; recomendado no Linux)",
                fix="make configure  # baixa o ggml-silero (corte por silêncio)",
            )
        )
    return results


def _check_claude() -> list[CheckResult]:
    persisted = load_persisted()
    if persisted.default_flow == "clipboard":
        return []
    node = shutil.which("node")
    claude = shutil.which("claude")
    return [
        CheckResult(
            "Claude CLI (fluxo claude_chat)",
            claude is not None and node is not None,
            f"node={'ok' if node else '—'} claude={'ok' if claude else '—'}",
            fix="npm install -g @anthropic-ai/claude-code && claude  # login interativo",
        )
    ]


def _check_torch_gpu() -> list[CheckResult]:
    code = "import torch; print(torch.__version__, torch.cuda.is_available())"
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return [CheckResult("PyTorch (TTS)", None, f"não verificado: {exc}")]
    if proc.returncode != 0:
        return [
            CheckResult(
                "PyTorch (TTS)",
                None,
                "torch não instalado (ok se TTS desabilitado)",
                fix="make configure  # instala o torch da sua GPU",
            )
        ]
    out = proc.stdout.strip()
    accelerated = out.lower().endswith("true")
    return [
        CheckResult(
            "PyTorch com GPU (TTS realtime)",
            accelerated,
            out,
            fix="make configure  # reinstala o torch CUDA/ROCm; confira o driver",
        )
    ]


# ─── Saída ───────────────────────────────────────────────────────────────────


def main() -> int:
    force_utf8_stdio()
    print("[doctor] Diagnóstico do ambiente VoiceMate\n")
    failures = 0
    for result in run_checks():
        if result.ok is None:
            mark = "—"
        elif result.ok:
            mark = "✓"
        else:
            mark = "✗"
            failures += 1
        detail = f"  ({result.detail})" if result.detail else ""
        print(f"  {mark} {result.label}{detail}")
        if result.ok is False and result.fix:
            print(f"      ↳ correção: {result.fix}")
    print()
    if failures:
        print(f"[doctor] {failures} problema(s) encontrados — correções sugeridas acima.")
    else:
        print("[doctor] Tudo certo! Rode: make run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
