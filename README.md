**English** | [Português](README.pt-BR.md)

# VoiceMate

> Press a hotkey, speak, paste. Local Whisper transcription straight into your clipboard — or routed through Claude and read back to you in your own voice.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

## Why

Cloud dictation is fast — until it's not. VoiceMate runs Whisper **locally** on your GPU, so your audio never leaves your machine and there's no network latency, monthly fee, or privacy trade-off. Press a hotkey, talk, paste anywhere.

## Features

- **Toggle hotkey** — press once to start, press again to stop and transcribe
- **Two flows, one mic** — `Ctrl+Alt+V` drops the transcription in the clipboard; `Ctrl+Alt+A` routes it to Claude (multi-turn) and reads the AI response back through TTS
- **Local transcription** — `faster-whisper` (CTranslate2) on NVIDIA/CPU, or `whisper.cpp` + Vulkan on AMD GPUs (~1.6 GB VRAM, large-v3-turbo) — the backend is picked automatically per GPU
- **GPU-accelerated, vendor-agnostic** — NVIDIA (CUDA) **and** AMD (Vulkan + ROCm) are both supported, with automatic CPU fallback. Idle VRAM ≈ 0 (STT runs as a subprocess; TTS loads lazily on first speech)
- **Pluggable TTS** — Claude's response is read aloud by [VoxCPM2](https://github.com/OpenBMB/VoxCPM) (2B params, voice design from a textual description, streaming). The architecture isolates each TTS engine so you can swap or remove it without touching the rest
- **Dual-clipboard with Win+V** — the AI flow copies the transcription first, then the response, so the Windows clipboard history shows both side by side for review
- **Stop decides the destination** — start with any hotkey; the hotkey you press to *stop* picks the handler (clipboard vs. Claude)
- **Mid-flight cancel** — pressing any hotkey while Claude is responding (or while TTS is speaking) cancels instantly and starts a new recording, preserving the conversation
- **Self-healing listener** — re-installs the global hotkey periodically to recover from silent Windows hook removal under load
- **Watchdog** — process-level health monitor with auto-restart on hangs
- **Configurable max recording** — guards against forgotten sessions (default: 10 min)
- **Audio feedback** — distinct beeps for start, warning, transcription complete, and AI response ready
- **Mouse trigger support** — use a side button instead of keyboard, if you prefer (clipboard flow only)

## Requirements

- One of the supported environments (the platform layer picks the right integrations automatically):
  - **Windows 10/11** native (NVIDIA recommended) — the original target, unchanged
  - **Linux** native, X11 or Wayland
  - **WSL2** (Ubuntu) on Windows 11 — the app runs **entirely inside the WSL**, with a tiny
    Windows-side hotkey script; recommended path for **AMD GPUs** (ROCm). See [docs/wsl2.md](docs/wsl2.md)
- Python 3.12 (the TTS flow via VoxCPM2 does not support 3.13 yet)
- [Poetry](https://python-poetry.org/docs/#installation)
- A GPU is optional but strongly recommended (required for TTS at decent latency):
  - **NVIDIA** with CUDA, **or**
  - **AMD** (RDNA — e.g. RX 7000/9000) via ROCm (Linux/WSL2) or ROCm-on-Windows (Adrenalin ≥ 26.2.2)
  - No GPU? It still runs on CPU (slower — consider `--no-tts`)
- **For the Claude flow only:** Node.js 18+ and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) authenticated locally

## Install

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup
```

`make setup` **detects your GPU** (NVIDIA / AMD / none), confirms with you, installs the matching PyTorch build (CUDA `cu128` for NVIDIA, ROCm for AMD, or CPU) plus the modules you pick, and remembers everything in `~/.config/voicemate/config.toml`. Re-run the picker anytime with **`make configure`** (e.g. after switching GPUs).

> **AMD note:** `make setup` installs the ROCm PyTorch (for VoxCPM/TTS) and downloads **whisper.cpp + Vulkan** (for transcription). The ROCm wheels are **not** on PyPI and the AMD Adrenalin driver (≥ 26.2.2) must already be installed — the setup warns if the driver looks missing. See "GPU backends" below.

### Modular install (extras)

`make setup` asks which modules you want. If you'd rather install non-interactively, the granular targets still work (note: these don't install the GPU build of PyTorch — run `make configure` afterwards, or use `make setup`):

| Command                                        | What it installs                                                       |
| ---------------------------------------------- | ---------------------------------------------------------------------- |
| `make setup_env_minimal`                       | Just **core**: voice → transcription → clipboard.                      |
| `make setup_env_claude`                        | Core + `claude-agent-sdk` (enables the `Ctrl+Alt+A` Claude flow).      |
| `make setup_env_tts`                           | Core + `voxcpm` + `soundfile` (TTS — heavy: ~5 GB of model weights).   |
| `make setup_env` *(legacy, assumes NVIDIA)*    | Core + Claude + TTS + CUDA PyTorch (`--extras all`).                   |
| `make setup_env_custom EXTRAS="claude tts"`    | Free combination of extras.                                            |

Extras (passed to `poetry install --extras`): `claude`, `tts`, `whisper-gpu` (AMD GPU transcription via `openai-whisper`), `all`.

If an extra is missing the app still starts and just disables the corresponding flow with an instructive warning (`extra 'claude' not installed`) — never a hard crash.

### Languages

Claude replies in PT-BR by default. To change:

```bash
# Switch the assistant to English
make run ARGS="--output-lang en"
```

Internally, the canonical prompt (written in English) has an `{output_lang}` placeholder that is filled at runtime — no translated copies of the prompt are kept.

**App messages themselves** (logs, CLI help text) are also localized via `gettext` + Babel. Default is PT-BR; switch with an env var:

```bash
# App logs in English
VOICEMATE_LANG=en make run
```

To edit / regenerate the translation catalog:

```bash
make i18n-extract     # extract _() strings into voicemate.pot
make i18n-update      # propagate new keys to existing .po files
make i18n-compile     # compile .po → .mo (gettext loads .mo at runtime)
```

Catalogs live in `app/i18n/locales/{pt_BR,en}/LC_MESSAGES/voicemate.po`.

### Code conventions

- **Identifiers, config keys, docstrings, new comments**: English (PEP 8).
- **LLM prompts**: canonical English with `{output_lang}` placeholder. No translated prompt copies.
- **User-facing strings** (logs, messages, helps): English as `msgid`, translations under `app/i18n/locales/<lang>/LC_MESSAGES/voicemate.po`. PT-BR is the default. Add new translations by marking with `_()` in code + `make i18n-extract && make i18n-compile`.

### Set up Claude Code (optional — only for the AI flow)

If you only want the clipboard flow (`Ctrl+Alt+V`), you can skip this section and run with `--no-claude-chat`.

For the AI flow (`Ctrl+Alt+A`), VoiceMate talks to Claude through the `claude-agent-sdk`, which **reuses the local `claude` CLI and its credentials** — no extra API key needed.

1. **Install Node.js 18+** (skip if you already have it). Download from [nodejs.org](https://nodejs.org/) or use a manager like `nvm-windows` / `fnm`.

2. **Install Claude Code globally:**
   ```bash
   npm install -g @anthropic-ai/claude-code
   ```

3. **Authenticate.** Run the CLI once and follow the interactive login (it opens a browser):
   ```bash
   claude
   ```
   Pick the auth method you use (Anthropic account or Claude Pro/Max). Type `/exit` once you're in to leave the chat — credentials are now saved locally.

4. **Verify it works:**
   ```bash
   claude --version
   claude -p "ping"
   ```
   If `ping` returns a Claude response, you're set.

Once Claude is authenticated, VoiceMate's AI flow picks it up automatically on `poetry run voice-mate`. If `claude` is missing or signed out, the AI flow is silently skipped and the clipboard flow keeps working.

### Set up TTS (VoxCPM2)

By default, Claude's response is read aloud using [VoxCPM2](https://github.com/OpenBMB/VoxCPM) — a 2B-parameter multilingual model (PT-BR supported) that takes a textual voice description rather than a reference audio.

- The `voxcpm` package is installed automatically when your environment runs on Python 3.12. On the first run, the model weights are downloaded from Hugging Face (a few GB — it takes a moment).
- The default voice is "a young Brazilian woman, natural and warm, with a calm pace" — customise with `--tts-voice "..."`.
- To disable TTS, run with `--no-tts` (the response still lands in the clipboard, and the triad beep comes back).
- If VoxCPM2 fails to boot (no CUDA, low disk, etc.), the app silently falls back to a no-TTS state — no action needed.

#### GPU backends (NVIDIA / AMD / CPU)

`torch`/`torchaudio` are **not** pinned in `pyproject.toml` — the right build depends on your card and OS. `make setup` (via `app.setup.gpu_bootstrap`) detects the platform + GPU and installs the correct build:

| Platform × GPU       | PyTorch build              | Transcription (best available first)                      | TTS (OmniVoice/VoxCPM) |
| -------------------- | -------------------------- | --------------------------------------------------------- | ---------------------- |
| Windows + NVIDIA     | CUDA `cu128`               | `faster-whisper` (CUDA)                                   | GPU (CUDA)             |
| Windows + AMD        | ROCm (`repo.radeon.com`)   | **whisper.cpp + Vulkan**                                  | GPU (ROCm)             |
| Linux/WSL2 + AMD     | ROCm (pytorch.org)         | **faster-whisper via CTranslate2-ROCm** → whisper.cpp + Vulkan → openai-whisper | GPU (ROCm) |
| Linux + NVIDIA       | CUDA `cu128`               | `faster-whisper` (CUDA)                                   | GPU (CUDA)             |
| any, no GPU          | CPU                        | `faster-whisper` (int8)                                   | CPU (slow)             |

**AMD on Linux/WSL2 (recommended for AMD):** the setup offers to build the
[CTranslate2-ROCm fork](https://github.com/arlo-phoenix/CTranslate2-rocm) — with it, transcription uses the
exact same `faster-whisper` engine as NVIDIA (identical quality). If you skip it (or the build fails), the
chain falls back automatically to **whisper.cpp** built with Vulkan (server mode keeps the model hot — fast
startup) with silero-VAD, then to `openai-whisper`. The choice is remembered (`ct2_rocm_ok` in the config);
`make configure` retries, `make stt-eval` measures quality objectively (WER + split-word detector).

**AMD on Windows:** transcription uses **whisper.cpp + Vulkan**, a small native binary plus a GGUF model
(large-v3-turbo fp16) downloaded to `~/.cache/voicemate/whispercpp/` (SHA-256 verified). The ROCm PyTorch
stack is still installed — but only for TTS.

To confirm GPU acceleration is live:

```bash
poetry run python -c "import torch; print('GPU:', torch.cuda.is_available())"
```

This must print `GPU: True` (on ROCm, AMD's HIP reports as `cuda` — so `True` is correct for AMD too). If it prints `False`:

- **NVIDIA:** update your driver (`nvidia-smi`); recent drivers (≥ 545) cover CUDA 12.8.
- **AMD:** install/update the Adrenalin driver (≥ 26.2.2), then run `make configure`.

If you have no GPU and only want the clipboard flow, run with `--no-tts`. VoxCPMSpeaker also prints a vendor-aware warning on startup when it detects PyTorch without acceleration.

You can override detection per run with `--gpu-backend {auto,nvidia,amd,cpu}`, `--whisper-backend {faster-whisper,whispercpp,openai-whisper}` and `--stt-strategy {auto,faster-whisper-rocm,whispercpp,openai-whisper}`.

### Platforms & triggers

The platform layer (`app/platform/`) detects where you are and picks the right hotkey mechanism and clipboard
integration — override with `--platform` / `--trigger`:

| Platform        | Hotkey trigger (default)                  | Clipboard            | Notes |
| --------------- | ----------------------------------------- | -------------------- | ----- |
| `windows`       | `keyboard-hooks` (keyboard/mouse libs)    | pyperclip            | Same behaviour as always (incl. listener keepalive) |
| `linux-x11`     | `pynput` (GlobalHotKeys)                  | pyperclip (xclip)    | `poetry install --extras linux` |
| `linux-wayland` | `evdev` (/dev/input — needs `input` group)| pyperclip (wl-copy)  | `sudo usermod -aG input $USER` |
| `wsl2`          | `socket` — local HTTP daemon + a tiny Windows-side hotkey script | WSLg sync (fallback `clip.exe`) | See [docs/wsl2.md](docs/wsl2.md) |

Default hotkeys are identical everywhere: `Ctrl+Alt+V` (clipboard) and `Ctrl+Alt+A` (Claude). On WSL2 they are
registered by `scripts/windows/voicemate-hotkeys.ahk` (or `.ps1`) which POSTs to the daemon — same
"the stop hotkey picks the handler" semantics. Run `make doctor` to validate mic/audio/trigger/GPU with
actionable fixes.

## Usage

```bash
make run
```

Default hotkeys:

- **`Ctrl+Alt+V`** — clipboard flow (transcription → clipboard)
- **`Ctrl+Alt+A`** — Claude flow (transcription → Claude → AI response in clipboard + TTS)

### Clipboard flow

1. Press `Ctrl+Alt+V` to start recording (start beep)
2. Speak naturally
3. Press `Ctrl+Alt+V` again to stop
4. The transcription is copied to your clipboard (double beep)
5. Paste with `Ctrl+V` anywhere

### Claude flow (multi-turn with voice)

1. Press `Ctrl+Alt+A` to start recording
2. Speak your prompt
3. Press `Ctrl+Alt+A` again to stop — VoiceMate transcribes, copies the transcription to the clipboard, sends it to Claude
4. The AI response replaces the clipboard content and VoxCPM2 starts reading it aloud (in PT-BR by default)
5. Press `Ctrl+Alt+A` again to ask a follow-up — the conversation continues in the same session

**Stop decides the destination:** you can start with `Ctrl+Alt+V` and stop with `Ctrl+Alt+A` (or vice-versa). The hotkey you press to *stop* picks the handler.

**Cancel while Claude is thinking or speaking:** pressing any hotkey while the AI is responding — or while TTS is reading aloud — cancels instantly and starts a new recording. The conversation context is preserved.

**Win+V history:** because both the transcription and the AI response pass through the clipboard, the Windows clipboard history (`Win+V`) shows both — useful when you want to compare what you said to what Claude answered.

### Options

```bash
# Pick a different Whisper model
poetry run voice-mate --model medium

# Custom hotkeys
poetry run voice-mate --hotkey "ctrl+shift+r" --claude-chat-hotkey "ctrl+shift+c"

# Disable the Claude flow (clipboard only)
poetry run voice-mate --no-claude-chat

# Give Claude a system prompt
poetry run voice-mate --claude-system-prompt "Você é um assistente de produtividade conciso."

# Cap the multi-turn session
poetry run voice-mate --claude-max-turns 20

# Disable TTS (response goes only to clipboard + beep)
poetry run voice-mate --no-tts

# Customise the TTS voice profile
poetry run voice-mate --tts-voice "A Brazilian man, deep and unhurried voice."

# Force CPU for TTS (slower but works without GPU)
poetry run voice-mate --tts-device cpu

# Save generated TTS audio to a directory
poetry run voice-mate --tts-save-dir ./tts_logs

# Force CPU for Whisper transcription (no GPU available)
poetry run voice-mate --cpu

# Override GPU detection / transcription backend for this run
poetry run voice-mate --gpu-backend amd                       # force AMD (ROCm)
poetry run voice-mate --gpu-backend nvidia --whisper-backend faster-whisper

# Use a mouse side-button instead (clipboard flow only)
poetry run voice-mate --input-method mouse --mouse-button x

# Tune watchdog and listener-keepalive
poetry run voice-mate --listener-refresh-seconds 30 --watchdog-timeout 60
```

### Models

| Model              | VRAM (GPU) | Speed     | Quality    |
| ------------------ | ---------- | --------- | ---------- |
| `tiny`             | ~75 MB     | Very fast | Basic      |
| `base`             | ~140 MB    | Fast      | Good       |
| `small`            | ~460 MB    | Moderate  | Very good  |
| `medium`           | ~1.0 GB    | Moderate  | Great      |
| `large-v3-turbo`   | ~1.5 GB    | Fast      | Excellent  |
| `large-v3`         | ~3.0 GB    | Slow      | Maximum    |

Default is `large-v3-turbo` — the best speed/quality balance, especially for mixed-language audio.

## Makefile

| Command            | Description                                   |
| ------------------ | --------------------------------------------- |
| `make setup`       | Detect platform + GPU, install matching PyTorch + modules, remember choice |
| `make configure`   | Re-run the GPU/module picker (e.g. after a GPU swap) |
| `make doctor`      | Environment diagnosis (mic/audio, trigger, whisper.cpp, GPU) with fixes |
| `make stt-eval`    | STT quality gate: WER + split-word detector vs local samples |
| `make setup_env`   | Legacy install (assumes NVIDIA + all extras)  |
| `make lock`        | Regenerate `poetry.lock` (after pyproject edits) |
| `make run`         | Run with default model (`large-v3-turbo`)     |
| `make run-large`   | Run with `large-v3`                           |
| `make run-turbo`   | Run with `large-v3-turbo`                     |
| `make format`      | Format code with Ruff                         |
| `make lint`        | Lint with Ruff + type-check with Mypy         |
| `make test`        | Run pytest suite                              |
| `make clean`       | Remove caches                                 |

## Architecture

```
app/
├── main.py                          # Entry point + CLI parsing + flow wiring
├── core/
│   └── config.py                    # Config dataclass + FlowConfig + TTSConfig
└── services/
    ├── recorder.py                  # Microphone capture (sounddevice)
    ├── transcriber.py               # Whisper inference (faster-whisper)
    ├── audio_feedback.py            # Cross-platform beeps
    ├── audio_player.py              # Queue-based audio player for TTS streaming
    ├── recording_session.py         # State machine: idle → recording → processing
    ├── transcription_handler.py     # Protocol + ClipboardHandler
    ├── claude_chat_handler.py       # Claude flow: send + dual clipboard + TTS + cancel
    ├── claude_runtime.py            # Sync ↔ asyncio bridge for claude-agent-sdk
    ├── tts.py                       # TextToSpeech Protocol + NullSpeaker
    ├── voxcpm_speaker.py            # VoxCPM2 speaker (streaming + cancel)
    ├── input_listener.py            # Keyboard / mouse trigger abstraction
    ├── multi_hotkey_listener.py     # Multiple global hotkeys with distinct callbacks
    ├── listener_keepalive.py        # Periodic hook re-install (Windows fix)
    └── watchdog.py                  # Process-level health monitor
```

### Why the listener-keepalive?

On Windows, low-level hooks (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) used by global hotkey libraries are **silently removed** by the OS if the hook callback exceeds `LowLevelHooksTimeout` (max 1000 ms on Windows 10+). Under high CPU load this happens with no notification ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)). VoiceMate re-registers the hotkey every 60 s by default — so even if the OS killed the hook, the next tick reinstalls it.

### Why pluggable TTS?

The architecture separates the **orchestrator** (`TextToSpeech` Protocol in `tts.py`) from the **concrete implementation** (`VoxCPMSpeaker`). This makes it easy to test other TTS libs later (edge-tts, ElevenLabs, Piper, etc.) — just create a new Protocol implementation and wire it via config. If a lib doesn't fit, you can delete only its file.

## Stack

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — Whisper optimized with CTranslate2
- **[sounddevice](https://python-sounddevice.readthedocs.io/)** — microphone capture
- **[keyboard](https://github.com/boppreh/keyboard)** / **[mouse](https://github.com/boppreh/mouse)** — global input hooks
- **[pyperclip](https://github.com/asweigart/pyperclip)** — clipboard access
- **[claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-python)** — Claude flow, on top of the local `claude` CLI
- **[voxcpm](https://github.com/OpenBMB/VoxCPM)** — multilingual TTS with voice design from a textual description
- **[soundfile](https://github.com/bastibe/python-soundfile)** — WAV read/write (optional, only used when saving TTS audio)

## Contributing

Issues and PRs welcome. Run `make all` (format + lint + test) before opening a PR.

## License

[MIT](LICENSE) © Álli Terhorst

Part of [NanoBR](https://github.com/nano-br) — open-source utilities for everyday productivity.
