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
    results += _check_claude()
    results += _check_torch_gpu()
    return results


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
    """Algum caminho de escrita no clipboard precisa existir no WSL.

    Com `appendWindowsPath=false` no /etc/wsl.conf o clip.exe some do PATH —
    o app usa o caminho absoluto via interop, mas o interop precisa estar
    habilitado (ou um utilitário Linux instalado).
    """
    from app.platform.clipboard import _WINDOWS_CLIP_EXE

    options: list[str] = []
    if shutil.which("wl-copy"):
        options.append("wl-copy")
    if shutil.which("xclip"):
        options.append("xclip")
    if shutil.which("clip.exe"):
        options.append("clip.exe (PATH)")
    elif _WINDOWS_CLIP_EXE.exists():
        options.append(str(_WINDOWS_CLIP_EXE))
    return [
        CheckResult(
            "Clipboard (escrita)",
            bool(options),
            ", ".join(options) or "nenhum mecanismo encontrado",
            fix=("Habilite o interop em /etc/wsl.conf ([interop] enabled=true) ou: sudo apt install -y wl-clipboard"),
        )
    ]


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
