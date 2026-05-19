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
- **Local transcription** — `faster-whisper` (CTranslate2 backend, 4–8× faster than PyTorch)
- **GPU-accelerated** — CUDA float16 by default, with CPU int8 fallback
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

- Windows 10/11 (primary target — Linux/macOS may work but are not the focus)
- Python 3.12 (the TTS flow via VoxCPM2 does not support 3.13 yet)
- [Poetry](https://python-poetry.org/docs/#installation)
- NVIDIA GPU with CUDA (optional but recommended — required for TTS at decent latency)
- **For the Claude flow only:** Node.js 18+ and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) authenticated locally

## Install

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup_env
```

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

#### About PyTorch and CUDA

`pyproject.toml` pins `torch` and `torchaudio` to the official PyTorch source with the **CUDA 12.8** build — so `make setup_env` installs the GPU-enabled version out of the box, no extra step needed.

To confirm:

```bash
poetry run python -c "import torch; print('CUDA:', torch.cuda.is_available())"
```

This must print `CUDA: True`. If it prints `False`, check that your NVIDIA driver is up to date (`nvidia-smi` shows the version) — recent drivers (≥ 545) already cover CUDA 12.8.

If you don't have an NVIDIA GPU and want to use the app for the clipboard flow only (no TTS), run with `--no-tts`. The VoxCPMSpeaker also prints a warning on startup if it detects PyTorch without CUDA — watch for that message.

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
| `make setup_env`   | Install dependencies via Poetry               |
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
