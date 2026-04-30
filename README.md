**English** | [Português](README.pt-BR.md)

# VoiceMate

> Press a hotkey, speak, paste. Local Whisper transcription straight into your clipboard.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type checked: mypy](https://img.shields.io/badge/types-mypy-blue.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)

## Why

Cloud dictation is fast — until it's not. VoiceMate runs Whisper **locally** on your GPU, so your audio never leaves your machine and there's no network latency, monthly fee, or privacy trade-off. Press a hotkey, talk, paste anywhere.

## Features

- **Toggle hotkey** — press once to start, press again to stop and transcribe
- **Local transcription** — `faster-whisper` (CTranslate2 backend, 4–8× faster than PyTorch)
- **GPU-accelerated** — CUDA float16 by default, with CPU int8 fallback
- **Self-healing listener** — re-installs the global hotkey periodically to recover from silent Windows hook removal under load
- **Watchdog** — process-level health monitor with auto-restart on hangs
- **Configurable max recording** — guards against forgotten sessions (default: 10 min)
- **Audio feedback** — beeps for start, warning, and completion
- **Mouse trigger support** — use a side button instead of keyboard, if you prefer

## Requirements

- Windows 10/11 (primary target — Linux/macOS may work but are not the focus)
- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)
- NVIDIA GPU with CUDA (optional but recommended)

## Install

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup_env
```

## Usage

```bash
make run
```

Default hotkey: **Ctrl+Alt+V**

1. Press `Ctrl+Alt+V` to start recording (start beep)
2. Speak naturally
3. Press `Ctrl+Alt+V` again to stop
4. The transcription is copied to your clipboard (double beep)
5. Paste with `Ctrl+V` anywhere

### Options

```bash
# Pick a different model
poetry run voice-mate --model medium

# Custom hotkey
poetry run voice-mate --hotkey "ctrl+shift+r"

# Force CPU (no GPU available)
poetry run voice-mate --cpu

# Use a mouse side-button instead
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
├── main.py                          # Entry point + CLI parsing
├── core/
│   └── config.py                    # Config dataclass
└── services/
    ├── recorder.py                  # Microphone capture (sounddevice)
    ├── transcriber.py               # Whisper inference (faster-whisper)
    ├── audio_feedback.py            # Cross-platform beeps
    ├── recording_session.py         # Session lifecycle + max-recording timeout
    ├── input_listener.py            # Keyboard / mouse trigger abstraction
    ├── listener_keepalive.py        # Periodic hook re-install (Windows fix)
    └── watchdog.py                  # Process-level health monitor
```

### Why the listener-keepalive?

On Windows, low-level hooks (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) used by global hotkey libraries are **silently removed** by the OS if the hook callback exceeds `LowLevelHooksTimeout` (max 1000 ms on Windows 10+). Under high CPU load this happens with no notification ([Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc)). VoiceMate re-registers the hotkey every 60 s by default — so even if the OS killed the hook, the next tick reinstalls it.

## Stack

- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — Whisper optimized with CTranslate2
- **[sounddevice](https://python-sounddevice.readthedocs.io/)** — microphone capture
- **[keyboard](https://github.com/boppreh/keyboard)** / **[mouse](https://github.com/boppreh/mouse)** — global input hooks
- **[pyperclip](https://github.com/asweigart/pyperclip)** — clipboard access

## Contributing

Issues and PRs welcome. Run `make all` (format + lint + test) before opening a PR.

## License

[MIT](LICENSE) © Álli Terhorst

Part of [NanoBR](https://github.com/nano-br) — open-source utilities for everyday productivity.
