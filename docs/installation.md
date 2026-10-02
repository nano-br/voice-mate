# Installation

Purpose: every way to install, update and uninstall VoiceMate, in full detail. Back to the [README](../README.md).

## Choose a path

| Path | Engine runs on | UI | Status |
|---|---|---|---|
| [Windows installer + engine in WSL2](#windows-installer-with-the-engine-in-wsl2) | WSL2 distro | Companion app (tray) | Experimental |
| [From source, Windows native](#from-source) | Windows | Command line, engine hotkeys | Battle-tested with NVIDIA |
| [From source, companion on Windows](#companion-from-source-windows) | WSL2 distro | Companion app from the repository | Experimental |
| [Linux](#linux-experimental) | Linux (X11 or Wayland) | Command line, optional companion | Experimental |

The companion app on Windows always talks to an engine inside WSL2 (mode "Start it in WSL"), or attaches to one that already runs (mode "Connect only"). A companion that starts a native Windows engine does not exist yet.

## Requirements

| Piece | Requirement |
|---|---|
| Engine | Python 3.12 exactly (`>=3.12,<3.13`: the AMD ROCm wheels are built for 3.12 only), [Poetry](https://python-poetry.org/docs/#installation), GNU `make`, `git`. On Windows, install GNU `make` first, for example with Chocolatey (`choco install make`) or Scoop (`scoop install make`) |
| Companion from source | Python 3.12 or 3.13. `make companion-venv` uses `py -3`, the newest Python installed: if that is another version, run `make companion-venv COMPANION_BOOTSTRAP="py -3.12"` |
| Windows installer | Windows 10 version 1809 or newer, 64-bit |
| GPU | Optional. NVIDIA (CUDA), AMD (ROCm or Vulkan, **experimental**) or CPU |
| Claude flow (**experimental**) | The Claude Code CLI installed and signed in (see [Claude Code CLI](#claude-code-cli-for-the-claude-flow)) |

## Windows installer with the engine in WSL2

The installer (`VoiceMate-Setup-x.y.z.exe`) contains the companion app only. The engine is a git checkout inside a WSL2 distro, prepared with `make setup`.

### 1. Prepare WSL2

1. Install WSL2 with Ubuntu and update it from PowerShell: `wsl --install -d Ubuntu`, then `wsl --update`. WSLg (audio and clipboard bridge) comes with current WSL. The engine needs Python 3.12, which Ubuntu 24.04 ships: on a newer Ubuntu, install Python 3.12 yourself.
2. Inside Ubuntu, install the system packages:
   ```bash
   sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils wl-clipboard \
                       git cmake build-essential libvulkan-dev glslc vulkan-tools \
                       spirv-headers spirv-tools glslang-tools espeak-ng
   ```
   `espeak-ng` is only needed by the Kokoro TTS engine. The Vulkan and SPIR-V packages are needed to build whisper.cpp.
3. Install Python 3.12 (Ubuntu 24.04 ships it), Poetry and `make`. Poetry and `make` must be on the `PATH` of a **login shell**: the companion runs `bash -lc 'cd ... && exec make run-engine ...'`.
4. With an AMD GPU: install the AMD Adrenalin driver 26.2.2 or newer on Windows and ROCm inside WSL. Follow [wsl2.md](wsl2.md#prerequisites).

### 2. Install the engine

```bash
git clone https://github.com/nano-br/voice-mate.git ~/voice-mate
cd ~/voice-mate
make setup
make doctor
```

When the "Engine folder:" setting is empty, the companion looks for a folder that holds both `Makefile` and `app/main.py`, in this order, under your WSL home folder:

`voice-mate`, `ai-lab/voice-mate`, `workspace/voice-mate`, `projects/voice-mate`, `src/voice-mate`, `code/voice-mate`, `dev/voice-mate`, `git/voice-mate`, `repos/voice-mate`

The first match is saved in the settings. Any other place works too: set "Engine folder:" in Settings > **General** (relative to your home folder, or absolute). The default WSL distro is used unless you set "WSL distro:".

### 3. Install the companion

1. Download `VoiceMate-Setup-x.y.z.exe` from the [latest release](https://github.com/nano-br/voice-mate/releases/latest).
2. The installer is not code-signed. Windows SmartScreen may show a warning: choose **More info**, then **Run anyway**.
3. The installer speaks English, Brazilian Portuguese, Spanish, Russian and Simplified Chinese. It installs for your user only, without an administrator prompt, into `%LOCALAPPDATA%\Programs\VoiceMate`, and adds VoiceMate to the Start menu.
4. Optional tasks: a desktop shortcut, and starting at sign-in (offered on the first install only; later, the "Start VoiceMate when I sign in" setting controls it). Both are off by default.
5. The last page can start VoiceMate and explains how to pin it: open Start, search for VoiceMate, right-click it and choose **Pin to taskbar**. Windows does not let installers pin apps.

### 4. First run

- The tray shows "Starting engine..." while the model loads (10 to 60 seconds), then "Listening for Ctrl+Alt+V". The very first start also downloads the Whisper model, because `make setup` does not prefetch it. The companion gives an engine start 240 seconds and stops retrying ("VoiceMate stopped retrying") after 3 start timeouts in a row, so with a slow connection run `make run` once in the engine folder inside WSL before the first start, and stop it with `Ctrl+C` when it has loaded.
- If the engine folder is not found, a notification says "Engine folder not found". Set the folder in Settings and VoiceMate tries again.
- On Windows 11, VoiceMate keeps its tray icon visible on the taskbar ("Always show the VoiceMate icon on the taskbar"), unless you hid it yourself in the Windows taskbar settings.
- A notification "Pin VoiceMate to the taskbar" repeats the pinning steps once.
- If you used the old hotkey scripts (`scripts/windows/voicemate-hotkeys.ps1` or `.ahk`), close them and remove their shortcut from `shell:startup`: the companion now owns `Ctrl+Alt+V` and `Ctrl+Alt+A`.
- If the `voicemate` systemd user service is enabled in WSL, the companion attaches to that engine and shows "Engine run by systemd" with the command that disables the service (`systemctl --user disable --now voicemate`).

## From source

This path runs the engine directly, with its own global hotkeys. It is the original Windows + NVIDIA path, and it is also the way to run on Linux.

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup
make doctor
make run ARGS="--output-lang en"
```

The engine defaults to Portuguese (`--output-lang pt-BR`): without a flag Whisper is pinned to Portuguese, and Claude's answers and the engine messages are in Portuguese too. Use `--output-lang en` (or another code), `--transcription-language en` to pin only what Whisper hears, or `VOICEMATE_LANG=en make run` for the engine messages only. The engine messages quoted in these guides are the English ones. See [usage.md](usage.md#dictation-language).

### What `make setup` does

`make setup` runs `poetry install`, compiles the translation catalogs (`make i18n-compile`), then starts the guided installer `python -m app.setup.gpu_bootstrap`:

1. Detects the platform (`windows`, `linux-x11`, `linux-wayland` or `wsl2`) and the GPU (`nvidia-smi` first, then AMD tools, else CPU).
2. Asks for the GPU vendor (NVIDIA, AMD or CPU), with the detected one as default.
3. Asks for the main flow: `clipboard` (dictation only) or `claude_chat` (also the Claude flow). The default answer (Enter) is `claude_chat`: choose `clipboard` (answer `1`) if you only want dictation, otherwise `make doctor` later fails the "Claude CLI (claude_chat flow)" check. For `claude_chat` it asks whether to enable TTS and which engine: `kokoro` (light, CPU, fixed voices; the default proposed here) or `omnivoice` (voice cloning, heavy on the GPU).
4. Installs the matching Poetry extras, then the PyTorch build for your GPU (table below).
5. AMD only: installs whisper.cpp with Vulkan, and on Linux or WSL2 offers to build CTranslate2-ROCm (slow build from source; optional).
6. Saves your choices in `~/.config/voicemate/config.toml`.
7. WSL2 only: offers to install the `voicemate` systemd user service (off by default).

| Vendor and platform | PyTorch build installed | Speech-to-text backends, in order of preference |
|---|---|---|
| NVIDIA (Windows, Linux) | CUDA `cu128` from download.pytorch.org | faster-whisper on CUDA |
| AMD, Windows (**experimental**) | ROCm 7.2.1 wheels from repo.radeon.com | whisper.cpp with Vulkan (prebuilt binary), openai-whisper (if the `whisper-gpu` extra is installed), faster-whisper on CPU |
| AMD, native Linux (**experimental**) | ROCm 7.2 manylinux wheels from repo.radeon.com | CTranslate2-ROCm (only if setup validated it), whisper.cpp with Vulkan (built from source), openai-whisper, CPU |
| AMD, WSL2 (**experimental**) | ROCm 7.2 manylinux wheels from repo.radeon.com | CTranslate2-ROCm (only if validated), openai-whisper, whisper.cpp, CPU |
| CPU | CPU build | faster-whisper (int8) |

On WSL2, openai-whisper comes before whisper.cpp because Vulkan inside WSL2 only sees `llvmpipe`, a software renderer. See [wsl2.md](wsl2.md#speech-to-text-performance-on-wsl2).

Run `make configure` to answer the questions again (for example after you change GPUs). Non-interactive install: `poetry run python -m app.setup.gpu_bootstrap --yes --vendor nvidia --extras "claude tts"`.

### Extras

PyTorch is not declared in `pyproject.toml` on purpose: `make setup` installs the right build last. The optional features are Poetry extras:

| Extra | Installs | For |
|---|---|---|
| `claude` | `claude-agent-sdk` | The Claude flow |
| `tts` | `omnivoice`, `soundfile` | TTS with OmniVoice (the engine used when nothing is saved) |
| `kokoro` | `kokoro`, `soundfile` | TTS with Kokoro (needs `espeak-ng`) |
| `voxcpm` | `voxcpm`, `soundfile` | TTS with VoxCPM2 |
| `whisper-gpu` | `openai-whisper`, `silero-vad` | Speech-to-text on AMD through PyTorch ROCm |
| `linux` | `pynput`, `evdev` | Global hotkeys on native Linux (added automatically there) |
| `ui` | `pyside6-essentials`, `pywin32` (Windows only) | The companion app |
| `all` | Everything above except `ui` | |

Older Makefile targets still work, but they do not install a GPU build of PyTorch: `make setup_env_minimal`, `make setup_env_claude`, `make setup_env_tts`, `make setup_env_custom EXTRAS="claude tts"`, and `make setup_env` (assumes NVIDIA, installs `all`). Run `make configure` after them.

A missing extra never crashes the engine: the flow is disabled and the log explains how to install it, for example "Claude flow disabled: extra 'claude' not installed.".

### Check the GPU

`make doctor` reports "PyTorch with GPU (realtime TTS)". By hand:

```bash
poetry run python -c "import torch; print('GPU:', torch.cuda.is_available())"
```

`GPU: True` is the expected answer on NVIDIA and on AMD (ROCm reports itself as `cuda`). If it says `False`: update the NVIDIA driver, or the AMD Adrenalin driver (26.2.2 or newer), then run `make configure`.

### Claude Code CLI (for the Claude flow)

The Claude flow is **experimental**. It uses `claude-agent-sdk`, which reuses the local `claude` CLI and its credentials, so no separate API key is needed.

1. Install Node.js 18 or newer.
2. Install the CLI: `npm install -g @anthropic-ai/claude-code`.
3. Run `claude` once and sign in.
4. Choose the `claude_chat` flow in `make setup` (or install the `claude` extra).

`make doctor` checks that `node` and `claude` are on the `PATH`. If the CLI cannot start, the engine log says "Failed to start Claude (run `claude login`)" and the clipboard flow keeps working.

## Companion from source (Windows)

The companion has its own virtual environment on Windows, with pinned versions; the engine stays in WSL2.

```powershell
make companion-venv   # once: .venv-companion from requirements/companion-dev.txt
make run-tray         # compiles the catalogs, then starts the companion
```

`make run-tray ARGS="--demo"` starts a demo with a fake engine that cycles through every state; it runs next to a real instance.

To build the installer yourself you need [Inno Setup](https://jrsoftware.org/isinfo.php) 6.3 or newer (`winget install JRSoftware.InnoSetup`):

```powershell
make companion-installer   # dist\VoiceMate (PyInstaller), then dist\installer\VoiceMate-Setup-<version>.exe
```

## Linux (experimental)

1. Install Python 3.12, Poetry, `make`, `git`, `libportaudio2`, and a clipboard tool (`wl-clipboard` on Wayland, `xclip` on X11).
2. Clone and run `make setup`, `make doctor` and `make run` as in [From source](#from-source). On native Linux `make setup` adds the `linux` extra (global hotkeys).
3. Wayland: the hotkeys use `evdev`, which reads `/dev/input`. Add your user to the `input` group and sign in again: `sudo usermod -aG input $USER`. X11 uses `pynput`.

Optional companion (tray icon and status window):

```bash
poetry install --extras ui
make run-tray
```

On Linux the engine keeps its own hotkeys: the "Hotkeys" tab shows them read-only. Without a system tray (for example GNOME without the AppIndicator extension), the status window is the main window. To add VoiceMate to the applications menu, from the repository root:

```bash
mkdir -p ~/.local/share/applications &&
  sed "s|@VOICEMATE_DIR@|$PWD|g" packaging/linux/voicemate-companion.desktop \
  > ~/.local/share/applications/voicemate-companion.desktop
```

The entry runs `make -C "<checkout>" run-tray`. Starting at sign-in is a companion setting (XDG autostart).

## Updating

- **Companion:** run the newer `VoiceMate-Setup-x.y.z.exe`. If VoiceMate is running, the installer asks, then closes it together with the engine it started. Your settings are kept.
- **Engine:** in the engine folder, run `git pull`, then `make setup` again (your saved choices are the defaults). In the companion, use **Engine** > **Restart engine**. A companion newer than the engine shows "The engine is older than this app. Restart or update it.".

## Uninstalling

- **Companion (Windows):** uninstall VoiceMate from the Windows settings (Apps). The uninstaller closes VoiceMate, removes the logs, the rendered sound cues, the **Not copied** list (`pending.json`) and the sign-in entry. It keeps your settings in `%APPDATA%\VoiceMate\companion.toml`; delete that folder by hand to remove them too.
- **Engine:** if you installed the systemd service, run `systemctl --user disable --now voicemate` and delete `~/.config/systemd/user/voicemate.service`. Remove the Poetry environment (`poetry env remove --all` in the engine folder), then delete the engine folder, `~/.config/voicemate` and `~/.cache/voicemate` (whisper.cpp files, voice seeds and AMD caches). The downloaded model weights live in the Hugging Face and openai-whisper caches (`~/.cache/huggingface` and `~/.cache/whisper`), the multi-gigabyte part: delete them too if nothing else uses them.
