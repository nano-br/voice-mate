# VoiceMate on WSL2 (AMD GPU through ROCm)

Purpose: how to run the VoiceMate engine inside WSL2, mainly with an AMD GPU through ROCm, and what to expect from that setup. This path is **experimental**: it was validated on one AMD card (Radeon RX 9070 XT, RDNA4, `gfx1201`). Back to the [README](../README.md).

The engine runs **entirely inside WSL** (Ubuntu): microphone capture, transcription on the GPU, TTS. Global Windows hotkeys do not reach background processes in WSL, so a Windows-side program owns the hotkeys and talks to the engine over a local HTTP API. That program is the **VoiceMate companion app** (recommended). The old PowerShell and AutoHotkey scripts still work as a legacy option.

```text
[Windows]  Ctrl+Alt+V / Ctrl+Alt+A
   |  VoiceMate companion (tray app): hotkeys, verified clipboard, sound cues, supervisor
   |  starts the engine with wsl.exe ... make run-engine, then talks HTTP on 127.0.0.1:47821
   v
[WSL2]  VoiceMate engine daemon (trigger=socket, API v2 with a token)
   |-- microphone through WSLg PulseAudio (RDPSource)
   |-- speech-to-text on the AMD GPU (openai-whisper on PyTorch ROCm; CTranslate2-ROCm optional)
   |-- publishes each result for the companion to deliver to the Windows clipboard
   `-- Claude + TTS (OmniVoice on the GPU, or Kokoro on the CPU), optional
```

## Prerequisites

1. **Windows 11 with an up-to-date WSL2** (`wsl --update`), with WSLg (included by default).
2. **AMD Adrenalin driver 26.2.2 or newer** on Windows.
3. **ROCm inside WSL** ([official AMD guide](https://rocm.docs.amd.com/projects/radeon/en/latest/docs/install/wsl/install-radeon.html)): `rocminfo` must list the GPU (for example `gfx1201`).
4. Audio and build packages in Ubuntu:
   ```bash
   sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils wl-clipboard \
                       git cmake build-essential libvulkan-dev glslc vulkan-tools \
                       spirv-headers spirv-tools glslang-tools espeak-ng
   ```
   - `espeak-ng` is only needed by the **Kokoro** TTS engine (Portuguese phonemes).
   - `wl-clipboard` (`wl-copy`) is the native WSLg clipboard path. It is more reliable than `clip.exe` through interop, which fails when systemd removes the WSLInterop binfmt ("Exec format error"). With the companion, the companion writes the Windows clipboard itself.
   - The last three packages are needed to compile the Vulkan shaders of whisper.cpp (`SPIRV-Headers`, `glslangValidator`). `libglslang-dev` does not exist under that name on Ubuntu 24.04: the packages above are enough.
5. Python 3.12, Poetry and `make`, available in a login shell (the companion runs `bash -lc`).

## Installation

Inside WSL:

```bash
git clone https://github.com/nano-br/voice-mate.git ~/voice-mate && cd ~/voice-mate
make setup     # detects WSL2 + AMD, installs PyTorch ROCm, openai-whisper and whisper.cpp (Vulkan),
               # offers the CTranslate2-ROCm build and saves your choices
make doctor    # diagnostics: WSLg microphone and audio, binaries, GPU, with fixes
```

Then install the companion on Windows: see [installation.md](installation.md#windows-installer-with-the-engine-in-wsl2). The companion finds the engine in `~/voice-mate` (or one of the other folders listed there), starts it, supervises it, registers the hotkeys, writes the Windows clipboard with verification, and restarts WSL when its audio gets stuck.

## Running

With the companion, there is nothing to run by hand: start VoiceMate on Windows. Press `Ctrl+Alt+V` anywhere in Windows, wait for the start cue, speak, press `Ctrl+Alt+V` again: the transcription lands in the Windows clipboard. `Ctrl+Alt+A` runs the Claude flow (answer in the clipboard and read aloud), which is **experimental** too.

Without the companion, `make run` in WSL starts the engine and prints the port of the daemon; one of the legacy scripts below then provides the hotkeys.

### How the text reaches the Windows clipboard

On WSL2 the WSLg clipboard bridge (and `clip.exe` through interop) is not reliable: sometimes the text never reaches Windows. So the **Windows side** writes the clipboard: the daemon publishes each result, and the companion writes the native clipboard, reads it back and compares, then acknowledges the result. A result that cannot be delivered goes to the **Not copied** list. While the companion holds the clipboard lease, the engine does not write the clipboard itself. The protocol is described in [architecture.md](architecture.md) and [companion-app.md](companion-app.md).

### Missing microphone or stuck WSLg audio

Opening the microphone never blocks the hotkey: it runs in the background and the trigger answers at once. If the microphone does not open within 6 seconds (no microphone on Windows, or WSLg PulseAudio stuck), the engine reports "Microphone unavailable", plays the error cue, returns to idle and publishes a `mic_unavailable` event. The next hotkey tries to open the microphone again; while an earlier attempt is still stuck in the audio server, it fails at once (without piling up another stuck attempt) and asks for `wsl --shutdown`. The start cue only plays once the microphone is really open.

Warning: when Windows loses its microphone, WSLg PulseAudio often **gets stuck for good** and does not recover even after the microphone is back (`pactl info` gives `Connection failure: Timeout`). Only `wsl --shutdown` fixes it. The companion detects this (the engine probes the audio every 20 seconds) and restarts WSL as set in "Restart WSL when audio fails:" ([troubleshooting.md](troubleshooting.md#wsl-audio-and-the-microphone)). Without the companion: run `wsl --shutdown`, then `make run` again.

### Autostart (systemd)

With the companion, the service is optional: the companion starts the engine itself. If the `voicemate` systemd user service is enabled, the companion attaches to the engine of the service and, on every start, shows the notification "Engine run by systemd" with the command that disables the service: `systemctl --user disable --now voicemate`. It does not disable the service for you.

`make setup` offers to install the service (off by default). By hand:

```bash
cp scripts/systemd/voicemate.service ~/.config/systemd/user/
# edit WorkingDirectory if the checkout is not in ~/voice-mate
systemctl --user daemon-reload
systemctl --user enable --now voicemate
loginctl enable-linger $USER       # keeps the service alive without an open terminal
journalctl --user -u voicemate -f  # logs
```

The service runs `poetry run voice-mate --trigger socket`, without a token.

## Speech-to-text performance on WSL2

**On WSL2, whisper.cpp with Vulkan does NOT use the GPU.** Inside WSL2, Mesa only exposes `llvmpipe`: a **software** Vulkan implementation that runs on the CPU. The GPU is only reachable through **ROCm/HIP** (`/dev/dxg`), not through Vulkan. Running `large-v3-turbo` on `llvmpipe` takes **minutes** per recording.

So on WSL2 the transcription chain prefers what really uses the GPU:

```text
faster-whisper on CTranslate2-ROCm (if validated)  ->  openai-whisper (PyTorch ROCm)  ->  whisper.cpp (last resort)  ->  CPU
```

On native Linux the order is CTranslate2-ROCm, whisper.cpp with Vulkan, openai-whisper, CPU, because there Vulkan does reach the GPU.

**openai-whisper** runs on the same PyTorch ROCm that already accelerates the TTS (OmniVoice); `make setup` installs it on AMD with Linux or WSL2 (extra `whisper-gpu`). A 5 second recording should transcribe in about 1 to 3 seconds.

To confirm the Vulkan device that whisper.cpp picked (when it is used): the server writes its log to `~/.cache/voicemate/whispercpp/server.log`, and the line `ggml_vulkan: found device:` shows `llvmpipe` on WSL2. `make doctor` flags it too.

### CTranslate2-ROCm (faster-whisper on the GPU): optional, best quality

It brings back the quality of faster-whisper on CUDA, on the AMD GPU. It is a heavy build (`make configure`, then accept CTranslate2-ROCm). Known risk on `gfx1201`: reports of a *memory access fault* (OpenNMT/CTranslate2#2021); the app already applies the workaround `CT2_CUDA_ALLOCATOR=cub_caching`. If the build or the validation fails, the chain falls back to openai-whisper by itself, and the decision is saved as `ct2_rocm_ok`. `hipcc` comes with the `rocm` use case of ROCm 7.2.

> whisper.cpp with **HIP** (instead of Vulkan) would solve this natively, but the pull request that adds `gfx120X` support (ggml-org/whisper.cpp#3757) is not merged yet.

## PyTorch ROCm: where the wheels come from

On Linux and WSL2 with AMD, `make setup` installs the **manylinux wheels from `repo.radeon.com`** (torch, torchvision, torchaudio and triton, with `numpy==1.26.4`): the combination AMD publishes and tests for WSL.

If the environment **already has** a `+rocm` torch that accelerates (a manually validated install), setup **does not reinstall over it**: it detects it and keeps it. To force a reinstall: `pip uninstall torch` inside the environment, then `make configure`.

> The ROCm track for WSL is **7.2** (package `7.2.70200`, installed with `amdgpu-install --usecase=wsl,rocm --no-dkms`). Do not use `7.2.4`: that is the native Linux track and has no `wsl` use case.

## Recommended environment variables (`~/.bashrc`)

```bash
export PULSE_SERVER=unix:/mnt/wslg/PulseServer          # WSLg microphone and audio
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True  # VRAM fragmentation
export FLASH_ATTENTION_TRITON_AMD_ENABLE="TRUE"         # flash attention through Triton (RDNA4)
```

The app already sets `PYTORCH_TUNABLEOP_*` and `MIOPEN_*` (cache in `~/.cache/voicemate`) and `CT2_CUDA_ALLOCATOR=cub_caching` when they are absent. Exporting them is optional and helps other apps.

> Do **not** set `HSA_OVERRIDE_GFX_VERSION` or `HSA_ENABLE_DXG_DETECTION`: the WSL track of ROCm recognizes the RX 9070 XT natively as `gfx1201`.

## Known WSL2 limitations (not bugs)

- `rocm-smi` and `amd-smi` **do not work** on WSL2; the app detects the GPU with `rocminfo`, which works. VRAM in use: `cat /sys/class/drm/card0/device/mem_info_vram_used`.
- Visible VRAM is below 16 GB (about 13 to 14 GB usable), because of the DXCore/librocdxg layer.
- About 10 to 20 % overhead compared with native Linux.

## TTS engines (Claude's spoken answer)

| Engine | Voice | Runs on | Real-time factor measured on an RX 9070 XT |
|---|---|---|---|
| `omnivoice` (used when nothing is saved) | clones a voice (diffusion) | GPU | about 0.7 (saturates the GPU while it synthesizes) |
| `kokoro` | fixed (about 82M parameters) | **CPU** by default | **about 0.24 (real time, GPU left free)** |

On WSL2 with AMD, **Kokoro runs on the CPU** on purpose: at a real-time factor of about 0.24 the synthesis **does not compete with transcription for the GPU**, which is what caused crackling. (On ROCm the Kokoro kernels are still slow, a real-time factor of about 1.8 on the GPU; the CPU is better in both respects.) To switch:

```bash
make run ARGS="--tts-engine kokoro --tts-kokoro-voice pf_dora"
# Portuguese voices: pf_dora (female), pm_alex and pm_santa (male)
# --tts-device cuda forces the GPU (not recommended here: slower)
```

Kokoro needs its extra (`poetry install --extras kokoro`). Portuguese `espeak-ng` data comes bundled through `espeakng-loader` (a dependency of misaki); the system `espeak-ng` package is a backup. Kokoro's Portuguese prosody sounds more robotic than OmniVoice's: that is the trade for real time without touching the GPU. Compare and keep the one you prefer.

## Microphone on WSLg

WSLg exposes the Windows microphone as a PulseAudio source (`RDPSource`):

```bash
export PULSE_SERVER=unix:/mnt/wslg/PulseServer   # put it in ~/.bashrc
pactl list sources short    # must list RDPSource
pactl list sinks short      # must list RDPSink (audio output)
```

If PortAudio (`sounddevice`) does not see the devices, install the ALSA to PulseAudio shim (`libasound2-plugins`, see the prerequisites). `make doctor` checks all of this.

> Privacy: in Windows Settings > Privacy > Microphone, check that desktop apps may use the microphone.

### TTS crackle (audio buffer)

On WSLg, audio output goes through PulseAudio over RDP, with high jitter. With the default buffer (about 34 ms) the player runs dry in the middle of a sentence and crackles. The app sets `PULSE_LATENCY_MSEC=200` at start and opens the stream with a larger block (about 340 ms in effect), which absorbs the jitter. To adjust it by hand:

```bash
export PULSE_LATENCY_MSEC=300   # more headroom (more latency) if it still crackles
```

## Legacy hotkey scripts (without the companion)

Before the companion, a script on Windows registered the hotkeys and called the daemon. The scripts still work, but only against an engine **without a token** (`make run`), never with the companion's `make run-engine`. Do not run them together with the companion: close them and remove their shortcut from `shell:startup`, or the companion reports "Used by another app".

- **PowerShell:** `powershell -ExecutionPolicy Bypass -File scripts\windows\voicemate-hotkeys.ps1`. It also sets the native clipboard through `/result`, with read-back. To start it with Windows, create a shortcut in `shell:startup` (see the script header). Its messages follow the daemon's language, or `-Language pt-BR|en|es|ru|zh-CN`, or `VOICEMATE_LANG` on Windows. Important warnings also become Windows notifications; `-NoToast` turns them off.
- **AutoHotkey v2:** double-click `scripts\windows\voicemate-hotkeys.ahk`. It **only triggers** the daemon; the native clipboard needs the PowerShell script.

The scripts use the v1 routes of the daemon: `POST /register` (a `client_id`), `POST /trigger {"flow"}` (returns the action taken: `started`, `stopped` or `restarted`), `GET /status` and `GET /result` (with `?scope=all|mine` and `?since=<seq>`; an item with `error` set is an error event such as `mic_unavailable`), `GET /health` (`pid`, `instance`, `lang`, `audio`) and `POST /shutdown`.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Hotkey does nothing | Engine stopped, or (legacy) Windows script not running | Check the tray state and `engine.log`; with the scripts, `make run` in WSL and run the `.ps1` |
| "daemon offline" (legacy script) | Engine stopped or another port | `make run`; check `--daemon-port` and the port `make run` prints |
| "Microphone unavailable" | No microphone on Windows, or WSLg PulseAudio stuck | Connect a microphone; if Windows has one, restart WSL (companion menu **Engine** > **Restart WSL...**, or `wsl --shutdown` and `make run`) |
| `/health` reports `"audio": "down"` | WSLg PulseAudio stuck | Restart WSL as above |
| No input device | WSLg microphone disabled | `wsl --update`; `make doctor` |
| Transcription 10 to 50 times slower | Fell back to the CPU | `make doctor` (PyTorch with GPU); `rocminfo` |
| CTranslate2-ROCm build failed | Incomplete ROCm development packages | `make doctor`; install the ROCm HIP SDK; `make configure` tries again |

More in [troubleshooting.md](troubleshooting.md).

## Transcription quality

An objective gate (word error rate and split words) against your own samples:

```bash
make stt-eval ARGS="--backends faster-whisper --save-baseline"  # once (reference)
make stt-eval                                                   # compares every backend
```

See `samples/ptbr/README.md`.
