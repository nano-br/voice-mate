"""Environment diagnostics (``make doctor``) — check, explain, never abort.

Each check prints ✓/✗ and, on failure, the exact fix command. Designed mainly
for WSL2 (mic/audio via PulseAudio RDP) and native Linux (input group for
evdev), but runs on any platform.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.console import force_utf8_stdio
from app.i18n import _
from app.platform.detect import default_trigger, detect_platform
from app.platform.kinds import PlatformKind, TriggerKind
from app.setup.persisted_config import load_persisted

_WSLG_PULSE_SOCKET = Path("/mnt/wslg/PulseServer")


@dataclass
class CheckResult:
    label: str
    ok: bool | None  # None = not applicable/skipped
    detail: str = ""
    fix: str = ""


def run_checks() -> list[CheckResult]:
    persisted = load_persisted()
    platform = persisted.platform or detect_platform()
    trigger: TriggerKind = persisted.trigger or default_trigger(platform)
    results: list[CheckResult] = [
        CheckResult(
            _("Platform detected"),
            True,
            _("{platform} (trigger: {trigger})").format(platform=platform, trigger=trigger),
        )
    ]
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
    """Kokoro needs espeak-ng (PT-BR G2P).

    Heuristic: only checks if the `kokoro` package is installed (the engine is a
    runtime choice via --tts-engine, not persisted — installing the extra
    signals intent to use it). Without espeak-ng, the Kokoro load fails on PT-BR.
    """
    from app.features import tts as tts_feature

    if not tts_feature.is_available("kokoro"):
        return []
    has_espeak = shutil.which("espeak-ng") is not None
    return [
        CheckResult(
            _("espeak-ng (Kokoro PT-BR)"),
            has_espeak,
            _("found") if has_espeak else _("missing — Kokoro PT-BR fails without it"),
            fix="sudo apt install -y espeak-ng",
        )
    ]


# ─── Checks ──────────────────────────────────────────────────────────────────


def _check_wslg_audio() -> list[CheckResult]:
    results: list[CheckResult] = []
    pulse = os.environ.get("PULSE_SERVER", "")
    socket_ok = _WSLG_PULSE_SOCKET.exists()
    results.append(
        CheckResult(
            _("WSLg PulseServer (socket)"),
            socket_ok,
            str(_WSLG_PULSE_SOCKET) if socket_ok else _("socket does not exist"),
            fix=_("Update WSL (`wsl --update` on Windows) and check guiApplications in .wslconfig."),
        )
    )
    results.append(
        CheckResult(
            _("PULSE_SERVER in the environment"),
            bool(pulse) or socket_ok,
            pulse or _("(empty — the ALSA shim may handle it, but exporting is recommended)"),
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
            _("ALSA→Pulse plugin (for PortAudio)"),
            libasound_pulse,
            fix="sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils",
        )
    )
    return results


def _check_wsl_clipboard() -> list[CheckResult]:
    """Clipboard writes on WSL2.

    Only the NATIVE utilities (wl-copy/xclip) are reliable on WSLg. clip.exe via
    interop fails when the WSLInterop binfmt is not registered (common with
    systemd) with "Exec format error" — that's why it doesn't count as ✓ alone.
    """
    native = [t for t in ("wl-copy", "xclip", "xsel") if shutil.which(t)]
    binfmt_ok = Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    if native:
        detail = ", ".join(native)
    elif binfmt_ok:
        detail = _("only clip.exe via interop (uncertain fallback)")
    else:
        detail = _("none (clip.exe fails without the WSLInterop binfmt)")
    results = [
        CheckResult(
            _("Native clipboard (WSLg)"),
            bool(native),
            detail,
            fix=_("sudo apt install -y wl-clipboard   # wl-copy works via WSLg and syncs with Windows"),
        )
    ]
    # Windows interop: when WSLInterop is not registered (systemd clears the
    # binfmt), clip.exe won't run — so the ONLY clipboard path is wl-copy (via the
    # WSLg bridge, which can become unstable over long sessions). Restoring interop
    # gives a DIRECT path to the Windows clipboard, independent of the bridge.
    results.append(
        CheckResult(
            _("Windows interop (clip.exe direct)"),
            binfmt_ok,
            _("registered") if binfmt_ok else _("WSLInterop missing — clip.exe won't run"),
            fix=_(
                "wsl --update (on Windows) fixes it in most cases; or register: "
                "sudo sh -c 'echo :WSLInterop:M::MZ::/init:PF > /proc/sys/fs/binfmt_misc/register'"
            ),
        )
    )
    return results


def _check_sounddevice() -> list[CheckResult]:
    """Mic (input) and playback (output) visible to PortAudio — in a subprocess
    so the doctor isn't polluted/crashed if the native lib blows up."""
    code = (
        "import sounddevice as sd, json; devs = sd.query_devices(); "
        "ins = [d['name'] for d in devs if d['max_input_channels'] > 0]; "
        "outs = [d['name'] for d in devs if d['max_output_channels'] > 0]; "
        "print(json.dumps({'in': ins[:3], 'out': outs[:3]}))"
    )
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [CheckResult(_("Audio (sounddevice)"), False, str(exc), fix=_("poetry install (core dependencies)"))]
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip()[-200:]
        return [
            CheckResult(
                _("Audio (sounddevice)"),
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
            _("Microphone (input device)"),
            has_in,
            ", ".join(data.get("in", [])) or _("no input device"),
            fix=_(
                "WSL2: enable the WSLg mic (wsl --update; `pactl list sources short` should show RDPSource). "
                "Linux: check PulseAudio/PipeWire."
            ),
        ),
        CheckResult(
            _("Audio output (output device)"),
            has_out,
            ", ".join(data.get("out", [])) or _("no output device"),
            fix=_("WSL2: `pactl list sinks short` should show RDPSink (wsl --update if not)."),
        ),
    ]


def _check_trigger(platform: PlatformKind, trigger: TriggerKind) -> list[CheckResult]:
    if trigger == "evdev":
        input_dir = Path("/dev/input")
        events = sorted(input_dir.glob("event*")) if input_dir.is_dir() else []
        readable = any(os.access(event, os.R_OK) for event in events)
        return [
            CheckResult(
                _("Access to /dev/input (evdev)"),
                readable,
                _("{count} device(s)").format(count=len(events)) if events else _("no event* devices"),
                fix=_("sudo usermod -aG input $USER  # then re-login (or `newgrp input`)"),
            )
        ]
    if trigger == "socket":
        return [
            CheckResult(
                _("Trigger via HTTP daemon"),
                True,
                _("register the hotkeys on Windows: scripts/windows/voicemate-hotkeys.ahk (or .ps1)"),
            )
        ]
    if trigger == "pynput":
        has_display = bool(os.environ.get("DISPLAY"))
        return [
            CheckResult(
                _("X11 session (DISPLAY) for pynput"),
                has_display,
                os.environ.get("DISPLAY", ""),
                fix=_("No X11? Use --trigger evdev (Wayland) — requires the input group."),
            )
        ]
    return [CheckResult(_("Trigger (Windows hooks)"), True, _("keyboard/mouse libs"))]


def _parse_vulkan_devices(summary: str) -> list[tuple[str, str]]:
    """(deviceName, deviceType) pairs from `vulkaninfo --summary` output."""
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
    """Does Vulkan see a real GPU? (relevant for the whisper.cpp backend).

    On WSL2 Mesa only exposes llvmpipe (software CPU) — whisper.cpp "Vulkan"
    there runs in software and takes minutes per utterance. The real GPU on WSL2
    is only reachable via ROCm (openai-whisper / CT2-ROCm).
    """
    if shutil.which("vulkaninfo") is None:
        return [CheckResult(_("Vulkan (GPU for whisper.cpp)"), None, _("vulkaninfo missing (vulkan-tools)"))]
    out = ""
    try:
        proc = subprocess.run(["vulkaninfo", "--summary"], capture_output=True, text=True, timeout=20)
        out = proc.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return [CheckResult(_("Vulkan (GPU for whisper.cpp)"), None, _("not verified: {exc}").format(exc=exc))]
    devices = _parse_vulkan_devices(out)
    real_gpus = [name for name, dtype in devices if "CPU" not in dtype.upper() and "llvmpipe" not in name.lower()]
    detail = ", ".join(f"{name} [{dtype}]" for name, dtype in devices) or _("no devices")
    fix = (
        _(
            "On WSL2 this is expected: Vulkan can't reach the GPU — use openai-whisper/CT2-ROCm "
            "(make configure). On native Linux: install the GPU's Vulkan driver (mesa-vulkan-drivers)."
        )
        if platform == "wsl2"
        else _("Install the GPU's Vulkan driver (mesa-vulkan-drivers) or use openai-whisper.")
    )
    return [CheckResult(_("Vulkan (GPU for whisper.cpp)"), bool(real_gpus), detail, fix=fix)]


def _check_whispercpp() -> list[CheckResult]:
    from app.core.config import Config
    from app.features import whispercpp

    directory = whispercpp.resolve_dir(Config())
    model = whispercpp.find_model(directory)
    exe = whispercpp.find_server_exe(directory) or whispercpp.find_exe(directory)
    vad = whispercpp.find_vad_model(directory)
    results = [
        CheckResult(
            _("whisper.cpp (binary + model)"),
            exe is not None and model is not None,
            _("{exe} / {model} in {dir}").format(
                exe=exe.name if exe else "—", model=model.name if model else "—", dir=directory
            ),
            fix=_("make configure  # downloads/builds whisper.cpp and the model"),
        )
    ]
    if exe is not None and model is not None:
        results.append(
            CheckResult(
                _("VAD (silero) for whisper.cpp"),
                vad is not None or sys.platform == "win32",
                vad.name if vad else _("no VAD model (ok on Windows; recommended on Linux)"),
                fix=_("make configure  # downloads ggml-silero (silence-based trimming)"),
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
            _("Claude CLI (claude_chat flow)"),
            claude is not None and node is not None,
            f"node={'ok' if node else '—'} claude={'ok' if claude else '—'}",
            fix=_("npm install -g @anthropic-ai/claude-code && claude  # interactive login"),
        )
    ]


def _check_torch_gpu() -> list[CheckResult]:
    code = "import torch; print(torch.__version__, torch.cuda.is_available())"
    try:
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return [CheckResult(_("PyTorch (TTS)"), None, _("not verified: {exc}").format(exc=exc))]
    if proc.returncode != 0:
        return [
            CheckResult(
                _("PyTorch (TTS)"),
                None,
                _("torch not installed (ok if TTS is disabled)"),
                fix=_("make configure  # installs the torch for your GPU"),
            )
        ]
    out = proc.stdout.strip()
    accelerated = out.lower().endswith("true")
    return [
        CheckResult(
            _("PyTorch with GPU (realtime TTS)"),
            accelerated,
            out,
            fix=_("make configure  # reinstalls the CUDA/ROCm torch; check the driver"),
        )
    ]


# ─── Output ──────────────────────────────────────────────────────────────────


def main() -> int:
    force_utf8_stdio()
    print(_("[doctor] VoiceMate environment diagnostics\n"))
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
            print(_("      ↳ fix: {fix}").format(fix=result.fix))
    print()
    if failures:
        print(_("[doctor] {count} problem(s) found — suggested fixes above.").format(count=failures))
    else:
        print(_("[doctor] All good! Run: make run"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
