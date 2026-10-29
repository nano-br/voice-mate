**English** | [Português](README.pt-BR.md) | [Español](README.es.md) | [Русский](README.ru.md) | [简体中文](README.zh-CN.md)

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/banner-dark.png">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/banner-light.png">
  <img alt="VoiceMate" src="docs/assets/brand/banner-light.png" width="640">
</picture>

**Press a hotkey, speak, paste. Local voice dictation to the clipboard, powered by Whisper on your own computer.**

[![CI](https://github.com/nano-br/voice-mate/actions/workflows/ci.yml/badge.svg)](https://github.com/nano-br/voice-mate/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/nano-br/voice-mate?include_prereleases&sort=semver&style=flat)](https://github.com/nano-br/voice-mate/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?style=flat&logo=python&logoColor=white)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Windows%2010%2F11%20%7C%20Linux%20%26%20WSL2%20(experimental)-0078D6?style=flat)](#platforms-and-gpus)
[![Languages](https://img.shields.io/badge/languages-en%20%7C%20pt--BR%20%7C%20es%20%7C%20ru%20%7C%20zh--CN-2EA44F?style=flat)](#languages)

[Download](https://github.com/nano-br/voice-mate/releases/latest) · [Quick start](#quick-start) · [Documentation](#documentation) · [Changelog](CHANGELOG.md)

<img src="docs/assets/screenshots/en/hero.png" width="763" alt="The VoiceMate status window with the Not copied and Recent lists, next to the tray menu">

</div>

## Why VoiceMate

I built VoiceMate because I believe one simple thing: the faster and easier it is to express an idea to an AI, and the more detail you give it, the better the result. Speaking is much faster than typing. When you think out loud, you explain an idea the way you would explain it to another person, with far more context than you would ever type.

VoiceMate turns speech into text instantly, ready for any AI tool: a prompt for a coding assistant, a message in a chat tool, anything you can paste. Press a hotkey, speak naturally, press it again and paste. Whisper runs locally, so your audio stays on your computer. Dictation needs no cloud service and no fee.

Dictation to the clipboard is the core of VoiceMate. I have used it for real work practically every working day since March 2026. It was built and battle-tested on Windows with an NVIDIA GPU. When I replaced my GPU with an AMD card, I adapted VoiceMate to AMD through WSL2 and Linux, and it is now tested there too. That path is still **experimental**.

A second module came later: a voice conversation with Claude. You speak, Claude answers, and the answer is read aloud with text-to-speech (TTS). This module is optional and **experimental**.

A third one is just starting: a **voice assistant**. It is one Claude conversation that knows what my other conversations are doing, asks them things for me and tells me when they answer, by voice. It lives in a conversation VoiceMate starts or in one I already have in Claude Desktop. It is **experimental** and off by default.

## Contents

[Features](#features) · [Screenshots](#screenshots) · [Quick start](#quick-start) · [Usage](#usage) · [Voice assistant](#voice-assistant-experimental) · [Configuration](#configuration) · [Architecture](#architecture) · [Troubleshooting](#troubleshooting) · [Documentation](#documentation) · [Contributing](#contributing) · [License](#license)

## Features

**Dictation (core)**
- One hotkey starts and stops the recording (`Ctrl+Alt+V` by default). The text lands in the clipboard, ready to paste.
- Local Whisper models, `large-v3-turbo` by default. No audio leaves your computer.
- The dictation language is pinned for stable results, and English technical terms inside other languages are still transcribed.
- Sound cues for start, transcribing, copied, warning and error. A recording stops by itself after 10 minutes (configurable).

**Windows companion app**
- A tray icon that shows the state at a glance, a status window with recent transcriptions, and settings for hotkeys, sound cues, notifications, language, transcription and the engine.
- Two speech-to-text modes, switchable from the tray menu ("Transcription") or Settings: **CPU** (leaves the GPU free for other work) and **GPU** (the fastest). Each mode keeps its own Whisper model. A mode that is not installed is installed by the app after it asks, showing the download size, and only inside the engine's own environment.
- Verified clipboard delivery: the app writes the text, reads it back and compares. A text that cannot reach the clipboard stays in **Not copied**, even after a restart.
- A supervisor that starts the engine inside WSL2, restarts it when it fails, and restarts WSL when its audio stops.
- A per-user installer (no administrator prompt) in 5 languages.

<a id="languages"></a>**Languages**: the companion, the engine messages and the installer speak English, Brazilian Portuguese, Spanish, Russian and Simplified Chinese.

<a id="platforms-and-gpus"></a>**Platforms and GPUs**
- Windows 10/11 with NVIDIA (CUDA): the original path, battle-tested. The engine runs natively from the command line.
- **Experimental:** the engine inside WSL2 (the companion's setup), AMD GPUs (ROCm, Vulkan) on WSL2, Linux and Windows, and Linux (X11, Wayland) with an optional companion.
- CPU fallback everywhere. `make setup` detects the platform and the GPU and installs the matching PyTorch build and speech-to-text backend.

**Voice conversation with Claude (experimental)**
- `Ctrl+Alt+A` sends your speech to Claude through the Claude Code CLI (your own sign-in: the transcribed text goes to Anthropic, the audio does not). The answer is copied to the clipboard and read aloud.
- TTS engines: OmniVoice (the engine used when nothing is saved), Kokoro (the one `make setup` proposes) and VoxCPM2. Or your own: any service that follows the [TTS provider protocol](docs/tts-provider-protocol.md) (Settings > Voice > Custom provider). The conversation continues across turns; any hotkey interrupts the answer and starts a new recording.

**Voice assistant (experimental, off by default)**
- One central Claude conversation in two modes: VoiceMate's own conversation (a Claude Code process that VoiceMate starts, also visible in Claude Desktop and on your phone) or a Claude Desktop conversation you already have (written by a brief keyboard "flash", Windows only). It lists, reads and sends to your other conversations and starts new ones in the background.
- Talk to it with `Ctrl+Alt+S` and hear it in the engine's voice (Kokoro or your own provider). Voice commands in English, Portuguese and Spanish read the last answer, switch to a conversation, send to it or talk directly to it. A window shows the chat and the answers waiting; a blue tray badge and an "Answer ready" sound tell you when one arrives, and `Ctrl+Alt+R` reads what is ready.
- It can change a fixed list of VoiceMate settings by voice, takes a personality (a file, a note in an Obsidian vault or a text you write) and uses a skill in English or Brazilian Portuguese that also works on its own in any Claude Code conversation. See [Voice assistant](#voice-assistant-experimental).

## Screenshots

| The tray menu | "Restart WSL?" and "Restart VoiceMate?" |
|:---:|:---:|
| <img src="docs/assets/screenshots/en/tray-menu.png" width="279" alt="The tray menu"> | <img src="docs/assets/screenshots/en/dialog-restart-wsl.png" width="516" alt="The Restart WSL? question"><br><img src="docs/assets/screenshots/en/dialog-language.png" width="466" alt="The Restart VoiceMate? question"> |

<p align="center"><img src="docs/assets/screenshots/en/settings-general.png" width="743" alt="The General tab of VoiceMate Settings"><br><sub>"VoiceMate Settings", "General" tab. Also: the <a href="docs/assets/screenshots/en/settings-hotkeys.png">"Hotkeys"</a> and <a href="docs/assets/screenshots/en/settings-sounds.png">"Sounds"</a> tabs.</sub></p>

<p align="center"><img src="docs/assets/screenshots/en/settings-voice.png" width="743" alt="The Voice tab of VoiceMate Settings, with the voice engine and the custom provider fields"><br><sub>"VoiceMate Settings", "Voice" tab: the voice engine that speaks the answers, including "Custom provider (HTTP)".</sub></p>

<p align="center"><img src="docs/assets/screenshots/en/assistant.png" width="568" alt="The VoiceMate assistant window with a sample conversation"><br><sub>The assistant window (**experimental**), opened from the tray menu entry "Assistant". Settings: the <a href="docs/assets/screenshots/en/settings-assistant.png">"Assistant"</a> tab. The "Transcription" box with the CPU and GPU choice is in the "General" tab above.</sub></p>

<p align="center"><img src="docs/assets/screenshots/en/assistant-inbox.png" width="568" alt="The assistant window with the Answers waiting group and the Waiting for list"><br><sub>The same window with the "Answers waiting" group and the "Waiting for" list.</sub></p>

<p align="center"><img src="docs/assets/screenshots/en/settings-assistant-sections.png" width="743" alt="The Assistant tab of VoiceMate Settings, scrolled to the Personality, Skill and Claude Desktop profile groups"><br><sub>The "Assistant" tab scrolled down: the "Personality", "Skill" and "Claude Desktop profile" groups.</sub></p>

<p align="center"><img src="docs/assets/screenshots/tray-states.png" width="584" alt="The tray icon in each state, on a dark and a light taskbar, and the badge shown when answers are waiting"><br><sub>The tray icon shows the state with a badge, and a blue one when answers are waiting. What each badge means: <a href="docs/usage.md#tray-icon-and-menu">Usage, tray icon and menu</a>.</sub></p>

## Quick start

### Windows, with the installer

The installer contains the companion app only. The engine runs inside a WSL2 distro, so set that up first (**experimental**; full guide in [docs/installation.md](docs/installation.md)).

1. In PowerShell, install WSL2 with Ubuntu: `wsl --install -d Ubuntu-24.04`, then open Ubuntu (if you already had another WSL distro, also run `wsl --set-default Ubuntu-24.04`, or set "WSL distro:" in Settings later). Inside it, install the audio packages; Python 3.12 (Ubuntu 24.04 ships it) and [Poetry](https://python-poetry.org/docs/#installation) must be on the `PATH` of a login shell too:
   ```bash
   sudo apt install -y libportaudio2 libasound2-plugins pulseaudio-utils wl-clipboard git make
   ```
2. Clone the engine and run the guided setup:
   ```bash
   git clone https://github.com/nano-br/voice-mate.git ~/voice-mate
   cd ~/voice-mate
   make setup    # for dictation only, answer 1 (clipboard) to "Which main flow?"
   make doctor   # every check should show a check mark
   ```
   The companion looks for the engine in `~/voice-mate` and a few other usual folders ([the list](docs/installation.md#2-install-the-engine)); anywhere else, set "Engine folder:" in Settings. With an AMD GPU, also install the AMD driver and ROCm for WSL ([docs/wsl2.md](docs/wsl2.md)).
3. Recommended: still in `~/voice-mate`, run `make run` once and stop it with `Ctrl+C` when it has loaded. The very first start downloads the Whisper model, and the companion gives an engine start only 240 seconds (it stops retrying after 3 start timeouts in a row), which a slow connection can exceed.
4. Download `VoiceMate-Setup-x.y.z.exe` from the [latest release](https://github.com/nano-br/voice-mate/releases/latest) and run it. The installer is not code-signed, so Windows SmartScreen may warn you: choose **More info**, then **Run anyway**. It installs for your user only, with no administrator prompt.
5. Start VoiceMate. The tray shows "Starting engine..." while the model loads (10 to 60 seconds once the model is downloaded), then "Listening for Ctrl+Alt+V".
6. Optional: pin it to the taskbar. Open Start, search for VoiceMate, right-click it and choose **Pin to taskbar**.

### From source

You need Python 3.12, [Poetry](https://python-poetry.org/docs/#installation), GNU `make` (on Windows, install it first, for example with Chocolatey or Scoop) and `git`.

```bash
git clone https://github.com/nano-br/voice-mate.git
cd voice-mate
make setup                           # detects platform and GPU, installs PyTorch and the modules you choose
make doctor                          # checks microphone, audio, hotkeys and GPU, and prints a fix for each problem
make run ARGS="--output-lang en"     # starts the engine with its own hotkeys (Windows native or Linux)
```

**The engine defaults to Portuguese** (`--output-lang pt-BR`), which sets the language Whisper hears, Claude's answers and the engine messages. Pass `--output-lang en` (or another code) as above; `--transcription-language` and `VOICEMATE_LANG` change one of them only ([configuration](docs/configuration.md)). The companion passes these flags itself, see [Usage](#usage).

To run the companion from source on Windows, with the engine in WSL2: `make companion-venv` once, then `make run-tray`. On Linux, run `make setup` and `make run` as above; for the optional tray and status window, `poetry install --extras ui` and then `make run-tray` ([details](docs/installation.md#linux-experimental)).

## Usage

| Hotkey | Tray menu action | What happens |
|---|---|---|
| `Ctrl+Alt+V` | "Dictate" | Speech to text, copied to the clipboard |
| `Ctrl+Alt+A` | "Ask Claude" | Speech to Claude, answer copied and read aloud (**experimental**) |
| `Ctrl+Alt+S` | "Talk to the assistant" | Speech to the assistant instead of the clipboard (**experimental**) |
| `Ctrl+Alt+W` | "Open the assistant window" | Opens or closes the assistant window |
| `Ctrl+Alt+R` | "Hear what's ready" | The assistant reads the answers that are waiting |

Press `Ctrl+Alt+V`, speak after the start cue, press `Ctrl+Alt+V` again, then paste with `Ctrl+V` anywhere. The hotkey that stops the recording decides where the text goes. A text that could not reach the clipboard waits in **Not copied** (tray menu and status window), where one click copies it. To quit, choose **Quit VoiceMate** in the tray menu; an engine that the companion did not start keeps running.

**Dictation language.** In Settings > **General**, "Dictation language:" decides the language you speak. The default is "Same as the interface"; "Detect automatically" lets Whisper detect each recording (Claude keeps answering in the interface language; with Kokoro, pin a language instead); or pick one language. The companion passes it to the engine it starts as `--transcription-language` and `--output-lang`, and changing it restarts the engine. An engine the companion only connects to (a systemd unit, or one started by hand) keeps its own flags.

The voice conversation with Claude needs the Claude Code CLI installed and signed in, and the `claude` module chosen in `make setup`. See [docs/usage.md](docs/usage.md#claude-flow-experimental).

**Transcription mode.** In Settings > **General** > "Transcription", "Run on:" is "CPU (leaves the GPU free)", "GPU (fastest)" or "Automatic (engine setup)". The tray menu "Transcription" does the same. Each mode has its own model ("CPU model:", "GPU model:"). Switching restarts the engine, and if the mode is not installed yet VoiceMate asks first (it names the download size), then installs it in a window that shows the output. From a terminal: `make stt-check` and `make stt-install MODE=cpu` (or `gpu`). Speed and accuracy numbers: [docs/configuration.md](docs/configuration.md#speech-to-text-modes).

### Voice assistant (experimental)

The assistant is one Claude conversation, the central one, that knows what your other conversations are doing and conducts them for you: it lists them, reads them, sends them a message, starts new ones in the background and tells you when they answer. It is off by default. In Settings > **Assistant**, "Assistant mode:" chooses where the central conversation lives.

| | VoiceMate's own conversation | A Claude Desktop conversation |
|---|---|---|
| What it is | A Claude Code process that VoiceMate starts and owns | A conversation you already have, chosen by its exact title in "Desktop conversation:" |
| How VoiceMate writes to it | Directly, through the process | A brief UI Automation "flash": the Desktop window comes forward, the draft is cleared, the text is pasted and sent, and the focus goes back (about 2 seconds) |
| Tools | The `voicemate` tools: list, read and send to conversations, start one, set its model, stop it | The Desktop's own session tools |
| Needs | The `claude` CLI and a Claude login | Claude Desktop open and the screen unlocked; Windows only |
| Phone | Remote Control is on | Not managed by VoiceMate |

1. **Requirements:** the Claude Code CLI (`claude` on the `PATH` of Windows, or its path in `VOICEMATE_CLAUDE_CLI`) and a Claude login on that computer (`claude auth login` once). Without them the assistant window says what is missing.
2. **Turn it on:** in Settings > **Assistant**, set "Assistant mode:" to "VoiceMate's own conversation" or "A Claude Desktop conversation" (then type its exact title), then OK. The tray menu gets an "Assistant" entry.
3. **Use it:** press `Ctrl+Alt+S`, speak, press `Ctrl+Alt+S` again, and it answers in the engine's voice. Or open "Assistant" and type (Enter sends, Shift+Enter adds a line). The window shows the last 30 messages, an "Answers waiting" group and a "Waiting for" list (what the assistant is waiting on in other conversations).

`Ctrl+Alt+S` talks, `Ctrl+Alt+W` opens the window and `Ctrl+Alt+R` reads what is ready. "Switch the assistant mode" and "Back to the assistant" have no default key; set them in Settings > **Hotkeys**.

**Voice commands.** Some phrases are detected without any AI, in English, Portuguese and Spanish: read the last answer, stop talking, go to a conversation, send to a conversation, "talk directly to the conversation X" and "back to the assistant". Direct mode sends what you say straight to that conversation until you go back. The phrases are in [usage](docs/usage.md#voice-commands).

**Answers from other conversations.** "Answers from other conversations:" is "Tell me the answer as soon as it arrives", "Say a short notice and keep the answer for later" (default), "Only play a sound and keep the answer" or "Just keep it". Kept answers wait in the inbox (50 at most, kept across restarts), in the window under "Answers waiting", with a blue badge on the tray icon and an "Answer ready" sound. Nothing is announced while you record, transcribe or hear a reply.

**Conversations the assistant starts.** At most 4 at once, with Remote Control on, remembered across restarts. "New conversations use:" and "With the effort:" set their model and effort, and "Permissions:" sets what they may do.

> [!WARNING]
> "Full access, never ask (risky)" (`bypassPermissions`) lets those conversations change any file and run any command without asking. Keep the default unless you trust what you ask them to do.

**The Desktop mode flash.** It takes the foreground for about 2 seconds, clears any draft in that prompt, and types nothing if the screen is locked or another window takes the focus. Leave the keyboard alone for those 2 seconds.

**Settings by voice.** The assistant can change 23 settings from a fixed list: the mode (between the two), model, effort, permissions, answer delivery, the model and effort of new conversations, the Desktop conversation, voice, voice commands, expression tags, your name, the personality source, the profile update, the skill language, the sound switches and volumes, the dictation language, the transcription mode and notifications.

Hotkeys, files and folders, the voice engine and its settings, and turning the assistant off are left out on purpose. The list: [usage](docs/usage.md#change-voicemate-by-voice).

**Personality and skill.** Under Personality, "Source:" is none, a Markdown file (for example a note in an Obsidian vault) or a text you write; "Vault folder:" says where your notes are, and the assistant reads them only when asked. "Skill language:" is automatic, English or Brazilian Portuguese, and "Custom skill:" replaces the skill with a folder of your own that holds a `SKILL.md`.

The skill is optional and shareable: copy `app/companion/assistant/skill/en/voicemate-assistant` (or `pt-BR`) into `~/.claude/skills` and ask for "the assistant" in any Claude Code conversation. Its settings are in `~/.voicemate/assistant.json`.

**Voice.** The assistant speaks with the engine's voice: Kokoro, or your own provider (Settings > **Voice**, `make tts-provider-check`, [protocol](docs/tts-provider-protocol.md)). If the engine cannot speak, the replies stay text and the short notices use the Windows voice.

**Claude Desktop profile.** A JSON file tells the Desktop mode where the controls are, so a Claude Desktop update is fixed by a new profile and not a release. VoiceMate looks for a newer one when the assistant starts (switchable); Settings > **Assistant** has **Check now**, **Import...**, **Export...** and **Use default**. See [desktop-profile.md](docs/desktop-profile.md).

The design is in [docs/research/voice-assistant-design.md](docs/research/voice-assistant-design.md), the Claude Desktop and phone tests behind it in [docs/research/claude-code-client.md](docs/research/claude-code-client.md), and every step in [docs/usage.md](docs/usage.md#voice-assistant-experimental).

## Configuration

| What | Where |
|---|---|
| Companion settings | Windows `%APPDATA%\VoiceMate\companion.toml`, Linux `~/.config/voicemate/companion.toml` |
| Engine choices from `make setup`, API token | `~/.config/voicemate/` (inside WSL for the companion; `%USERPROFILE%\.config\voicemate\` for an engine run natively on Windows) |
| Engine HTTP API | `127.0.0.1:47821` |
| Logs (`companion.log`, `engine.log`) | Windows `%LOCALAPPDATA%\VoiceMate\logs`, Linux `~/.local/state/voicemate/logs` |
| **Not copied** list | `pending.json` next to the `logs` folder |
| Assistant settings (the skill reads them) | `~/.voicemate/assistant.json` in your user folder |
| Assistant session, inbox, conversations it started, personality text and Desktop profile cache | `assistant-session.json`, `assistant-inbox.json`, `assistant-conversations\`, `assistant-personality.md`, `desktop-profile\` and `assistant\` next to `pending.json` |

Every engine flag, every settings key and every environment variable: [docs/configuration.md](docs/configuration.md).

## Architecture

VoiceMate has two parts. The **engine** records, transcribes and runs the Claude flow; it is a Python daemon with a local HTTP API. The **companion** is a PySide6 tray app on Windows that owns the hotkeys and the clipboard and supervises the engine inside WSL2. The engine also runs alone from the command line. The diagrams below are simplified overviews; each one links to the full diagram in [docs/architecture.md](docs/architecture.md), which also has the component diagrams, the HTTP API and the design choices.

<details>
<summary>System context (overview)</summary>

```mermaid
flowchart TB
    user(["User"])
    subgraph pc["Your PC"]
        vm["VoiceMate<br/>local dictation"]
        desk["Desktop<br/>hotkeys, clipboard, tray"]
        audio["Microphone and speakers"]
        gpu["GPU or CPU<br/>runs Whisper"]
    end
    subgraph net["Internet, optional"]
        hub["Model downloads"]
        claude["Claude<br/>through Claude Code CLI"]
    end
    user -->|"Hotkey, speech"| vm
    user -->|"Pastes the text"| desk
    vm -->|"Writes the clipboard, shows the tray"| desk
    vm -->|"Records"| audio
    vm -->|"Transcribes"| gpu
    vm -.->|"Downloads models"| hub
    vm -.->|"Experimental: asks"| claude

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class user person
    class vm system
    class desk,audio,gpu external
    class hub,claude optional
```

Overview. Full diagram: [docs/architecture.md, System context](docs/architecture.md#1-system-context).

</details>

<details>
<summary>Containers, Windows with WSL2 (overview)</summary>

```mermaid
flowchart LR
    user(["User"])
    subgraph win["Windows"]
        ui["Companion UI<br/>tray, status, settings"]
        core["Companion core<br/>hotkeys, supervisor, delivery"]
        clip["Windows clipboard"]
    end
    subgraph wsl["WSL2 distro"]
        api["Engine daemon<br/>HTTP API, 127.0.0.1:47821"]
        stt["Whisper backend"]
        tts["TTS, optional"]
    end
    gpu["GPU"]
    user -->|"Tray menu"| ui
    user -->|"Hotkey"| core
    ui <--> core
    core -->|"Writes and verifies"| clip
    core -->|"Starts with wsl.exe"| api
    core <-->|"HTTP, token, events"| api
    api --> stt --> gpu
    api -.-> tts

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class user person
    class ui,core,api,stt container
    class clip,gpu external
    class tts optional
```

Overview. Full diagram: [docs/architecture.md, Containers](docs/architecture.md#2-containers).

</details>

<details>
<summary>One dictation, step by step (overview)</summary>

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant C as Companion
    participant E as Engine
    participant CB as Clipboard
    rect rgba(67,141,213,0.12)
        U->>C: Hotkey
        C->>E: Start recording
        E-->>C: Microphone live
        C->>U: Start cue
    end
    rect rgba(106,61,154,0.12)
        U->>C: Hotkey again
        C->>E: Stop recording
        E->>E: Whisper transcribes
        E-->>C: Text to deliver
    end
    rect rgba(46,125,50,0.12)
        C->>CB: Write, read back, compare
        alt Text verified
            C->>E: ACK delivered
            C->>U: Ready cue
        else Clipboard locked by another app
            C->>C: Keep the text in Not copied
            C->>E: ACK failed
            C->>U: Error cue and notification
        end
    end
```

Overview. Full diagram: [docs/architecture.md, One dictation](docs/architecture.md#4-runtime-one-dictation).

</details>

<details>
<summary>Tray states (overview)</summary>

```mermaid
stateDiagram-v2
    [*] --> Stopped
    Stopped --> Starting : App starts
    Starting --> Running : Engine ready
    Starting --> Error : No engine folder
    Running --> Restarting : Engine failed or WSL restart
    Restarting --> Running : Engine ready
    Restarting --> Error : Too many failures
    Error --> Restarting : Manual restart
    Running --> Stopped : Quit
    state Running {
        [*] --> Idle
        Idle --> Recording : Hotkey
        Recording --> Transcribing : Hotkey again
        Recording --> Idle : Cancel
        Transcribing --> Ready : Text copied
        Transcribing --> Answering : Claude flow
        Answering --> Ready : Answer copied
        Ready --> Idle : After 3 s
        Idle --> Warning : No mic or audio down
        Warning --> Idle : Audio back
    }

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class Stopped external
    class Starting,Restarting,Warning warn
    class Running,Idle,Ready ok
    class Error bad
    class Recording,Transcribing,Answering container
```

Overview. Full diagram: [docs/architecture.md, Tray states](docs/architecture.md#5-tray-states).

</details>

<details>
<summary>Voice assistant, experimental (overview)</summary>

```mermaid
flowchart LR
    user(["You"])
    subgraph pc["Your PC, Windows"]
        win["Assistant window<br/>chat, answers waiting"]
        svc["Assistant service<br/>in the companion"]
        own["Own conversation<br/>Claude Code, voicemate tools"]
        dsk["Desktop conversation<br/>written by a keyboard flash"]
        other["Your other<br/>conversations"]
        inbox[("Inbox<br/>answers waiting")]
        eng["Engine<br/>speech in, voice out"]
    end
    phone["Phone"]
    user -->|"Talk hotkey"| eng
    eng -->|"Transcript"| svc
    user --> win
    win --> svc
    svc -->|"Prompts"| own
    svc -->|"Flash, then reads"| dsk
    own -->|"List, read, send, start"| other
    dsk -->|"Desktop session tools"| other
    other -->|"Answers"| svc
    svc -->|"Holds or announces"| inbox
    svc -->|"Voice block"| eng
    eng -->|"Speaks, answer cue"| user
    own <-->|"Remote Control"| phone

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    class user person
    class win,svc,own,dsk,eng container
    class inbox store
    class other,phone external
```

Both modes share the same loop: the assistant reaches your other conversations, the answers come back through the service, and the service speaks them or keeps them in the inbox. Blue: VoiceMate; teal: stored data; grey: outside VoiceMate. Full diagram: [docs/architecture.md](docs/architecture.md#61-voice-assistant-experimental).

</details>

<details>
<summary>Engine supervisor and WSL audio recovery (overview)</summary>

```mermaid
flowchart TD
    launch(["Launch"]) --> q_health{"Engine already answers?"}
    q_health -- "Yes" --> attach["Attach to it, wait until ready"] --> healthy
    q_health -- "No" --> q_dir{"Engine folder found?"}
    q_dir -- "No" --> failed["Error<br/>wait for a manual restart"]
    q_dir -- "Yes" --> spawn["Start make run-engine<br/>in WSL"]
    spawn --> q_ready{"Ready within 240 s?"}
    q_ready -- "Yes" --> healthy["Healthy<br/>probe every 5 s"]
    q_ready -- "No" --> q_breaker
    healthy -->|"3 missed probes or exit"| q_breaker{"Too many restarts?"}
    q_breaker -- "No" --> backoff["Wait 2 to 120 s"]
    backoff --> launch
    q_breaker -- "Yes" --> failed
    healthy -->|"WSL audio stopped"| q_policy{"Restart WSL when audio fails"}
    q_policy -- "Never" --> nothing["Do nothing"]
    q_policy -- "Ask first" --> ask["Ask the user"]
    q_policy -- "Automatically" --> q_others{"Other distros running?"}
    q_others -- "Yes" --> ask
    q_others -- "No" --> wslrestart["wsl --shutdown"]
    ask -- "Restart WSL" --> wslrestart
    wslrestart --> launch

    classDef person fill:#08427b,stroke:#052e56,color:#ffffff
    classDef system fill:#1168bd,stroke:#0b4884,color:#ffffff
    classDef container fill:#438dd5,stroke:#2e6295,color:#ffffff
    classDef component fill:#85bbf0,stroke:#5d82a8,color:#000000
    classDef store fill:#00838f,stroke:#005f66,color:#ffffff
    classDef external fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff
    classDef optional fill:#8a8a8a,stroke:#6b6b6b,color:#ffffff,stroke-dasharray:5 5
    classDef ok fill:#2e7d32,stroke:#1b5e20,color:#ffffff
    classDef warn fill:#f9a825,stroke:#c17900,color:#000000
    classDef bad fill:#c0392b,stroke:#922b21,color:#ffffff
    class launch system
    class q_health,q_dir,q_ready,q_breaker,q_policy,q_others component
    class attach,healthy ok
    class failed bad
    class spawn,backoff,wslrestart container
    class ask warn
    class nothing external
```

Overview. Full diagram: [docs/architecture.md, Supervisor](docs/architecture.md#6-supervisor-on-windows-and-wsl2).

</details>

## Troubleshooting

| Symptom | What to do |
|---|---|
| "Starting engine..." for a long time | The first start downloads the model, which can take minutes (run `make run` once in WSL, see [Quick start](#windows-with-the-installer)). Later starts take 10 to 60 seconds. Open **Engine** > **Open logs** and read `engine.log`. |
| "Engine folder not found" | Clone the engine into one of the [usual folders](docs/installation.md#2-install-the-engine), or set "Engine folder:" in Settings > **General**. |
| A hotkey says "Used by another app" | An old VoiceMate hotkey script may still run. Close it and remove it from `shell:startup`, or choose other hotkeys. |
| "WSL audio stopped" or "No microphone" | Connect a microphone. VoiceMate restarts WSL as set in "Restart WSL when audio fails:"; to do it by hand, use **Engine** > **Restart WSL...**. |
| A text did not reach the clipboard | It is in **Not copied** (tray menu and status window). Click it to copy it again. |
| English speech comes out wrong, or engine messages are in Portuguese | An engine started by hand defaults to Portuguese: add `ARGS="--output-lang en"` to `make run`. |
| The GPU is busy with other work | Choose the CPU mode: Settings > **General** > "Transcription", or tray menu > "Transcription". VoiceMate asks before it installs a mode that is missing. |
| The assistant says "Claude Code was not found" or "Claude Code is not signed in" | Install Claude Code (or set `VOICEMATE_CLAUDE_CLI`), or run `claude auth login` in a terminal, then press **Restart** in the assistant window. More in [docs/troubleshooting.md](docs/troubleshooting.md#voice-assistant-experimental). |
| The Desktop mode says "No Claude Desktop conversation is chosen" or "The assistant could not be set up" | "Desktop conversation:" is empty, or no Claude Desktop conversation has that exact title | Type the title exactly as in the Claude Desktop sidebar, open Claude Desktop, then press **Restart**. More in [docs/troubleshooting.md](docs/troubleshooting.md#voice-assistant-experimental). |
| Something else on the engine side | Run `make doctor` in the engine folder. It prints a fix for each failed check. |

More problems and the exact messages: [docs/troubleshooting.md](docs/troubleshooting.md).

## Documentation

- [Installation](docs/installation.md): every install path, updating and uninstalling.
- [Usage](docs/usage.md): flows, hotkeys, tray icon, transcription modes, models, voice options and the voice assistant.
- [Configuration](docs/configuration.md): engine flags, settings files, environment variables, speech-to-text modes and benchmark.
- [Troubleshooting](docs/troubleshooting.md): `make doctor`, WSL audio, logs, known messages.
- [Architecture](docs/architecture.md): diagrams and design choices.
- [WSL2 and AMD](docs/wsl2.md) (experimental), [Companion design](docs/companion-app.md), [Brand](docs/brand.md), [Releasing](docs/releasing.md).
- [Claude Desktop profile](docs/desktop-profile.md) and [TTS provider protocol](docs/tts-provider-protocol.md): the file that tells the Desktop mode where the controls are, and the HTTP contract for your own voice.
- Research (experimental work): [Voice assistant design](docs/research/voice-assistant-design.md) and [VoiceMate as a Claude Code client](docs/research/claude-code-client.md).

## Contributing

Issues and pull requests are welcome. Every dev command goes through the Makefile: `make format lint test` for the engine (Ruff and strict mypy), and `make companion-lint companion-test` for the companion. Every user-facing string exists in 5 languages through gettext. Commits follow Conventional Commits and explain the context. Read [CONTRIBUTING.md](CONTRIBUTING.md) first.

## Acknowledgements

VoiceMate stands on the work of many projects: [Whisper](https://github.com/openai/whisper) by OpenAI, [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [CTranslate2-ROCm](https://github.com/arlo-phoenix/CTranslate2-rocm), [PySide6](https://doc.qt.io/qtforpython-6/), [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice), [Kokoro](https://github.com/hexgrad/kokoro), [VoxCPM](https://github.com/OpenBMB/VoxCPM), the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) with [Claude Code](https://docs.claude.com/en/docs/claude-code), [Inno Setup](https://jrsoftware.org/isinfo.php) and [PyInstaller](https://pyinstaller.org/).

## License

[MIT](LICENSE) © Álli Terhorst. Part of [NanoBR](https://github.com/nano-br): open source utilities for everyday productivity.
