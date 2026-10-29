# Changelog

All notable changes to VoiceMate are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- [docs/voice-assistant.md](docs/voice-assistant.md): the whole voice assistant flow with Claude in one
  guide, with diagrams (the two modes, how it conducts other conversations, how answers come back,
  direct mode, the voice and safety). The README section is now a short summary that links to it.

### Fixed

- The voice assistant said "Claude Code was not found" when VoiceMate started at sign-in: the
  `claude` command was installed in `~/.local/bin` (or `%APPDATA%/npm`), which only a shell profile
  adds to PATH. VoiceMate now also looks in the installers' own folders. A start that failed this way
  now says "The assistant could not start" instead of asking to try again in a moment.

## [0.2.0] - 2026-10-29

### Added

- Voice assistant (experimental, off by default): one Claude conversation, the central one, that
  conducts your other conversations. Two modes in Settings > Assistant: "VoiceMate's own
  conversation" (a Claude Code process VoiceMate starts with the `voicemate-assistant` skill and the
  `voicemate` tools to list, read and send to conversations, start one in the background, set the
  model of one it started and stop it; shown in Claude Desktop and on the phone through Remote
  Control) and "A Claude Desktop conversation" (a conversation you already have, written by a
  brief UI Automation typing that takes the foreground for about 2 seconds, read from its transcript;
  needs the Desktop open and the screen unlocked, and clears any draft in that prompt; Windows only).
  A tray entry and a window with the last 30 messages and a message box. Needs the `claude`
  command and a Claude login on this machine.
- Assistant hotkeys, in the Hotkeys tab: "Talk to the assistant" (`Ctrl+Alt+S`, the recording goes to the
  assistant instead of the clipboard), "Open the assistant window" (`Ctrl+Alt+W`), "Hear what's ready"
  (`Ctrl+Alt+R`), "Switch the assistant mode" and "Back to the assistant" (no default).
- Direct mode and voice commands in English, Portuguese and Spanish: read the last answer, go to a
  conversation, send to a conversation, talk directly to a conversation, back to the assistant, stop
  talking. No AI decides them.
- Answers from other conversations: "Answers from other conversations" is "Tell me the answer as soon
  as it arrives", "Say a short notice and keep the answer for later" (default), "Only play a sound and
  keep the answer" or "Just keep it". Held answers wait in an inbox (50 at most, kept across restarts)
  shown in the assistant window under "Answers waiting", with a blue badge and "Answers waiting: N" on
  the tray icon, an "Answer ready" sound cue, and "Hear what's ready" (tray menu, hotkey or by asking)
  for a single spoken summary of them all.
- Conversations the assistant starts (at most 4 at once, Remote Control on, remembered across restarts)
  with "New conversations use" and "With the effort" as their model and effort, and "Permissions"
  (`default`, `acceptEdits`, or `bypassPermissions`, which gives them full access to the computer).
- Settings by voice: the assistant can change a fixed catalog of settings (assistant mode, model,
  effort, permissions, answer delivery, new conversation model and effort, Desktop conversation, voice,
  voice commands, expression tags, name, personality source, Desktop profile update, skill language,
  sound and answer sound switches and volumes, dictation language, transcription mode, notifications).
  Hotkeys, files and folders, the voice engine and the engine settings are left out on purpose.
- Personality: none, a Markdown file (for example a note in an Obsidian vault) or a text you write in
  the tab. "Vault folder" tells the assistant where your notes are; it reads them only when asked.
- The skill comes in English and Brazilian Portuguese ("Skill language"), and "Custom skill" replaces
  it with a folder of your own that holds a `SKILL.md`. It still works by itself in any Claude Code
  conversation (`app/companion/assistant/skill/{en,pt-BR}/voicemate-assistant`).
- Claude Desktop profile ([docs/desktop-profile.md](docs/desktop-profile.md)): a JSON file that tells the
  driver where the Desktop's controls are, so a Desktop update is fixed by a new profile and not a
  release. VoiceMate checks the repository for a newer one when the assistant starts (switchable), and
  Settings > Assistant can check now, import, export or go back to the default. Guards in code stay:
  only the Claude Desktop process, five keys, and only while it owns the foreground.
- The assistant's docs: usage, configuration, the companion contract and the architecture diagram
  (`docs/architecture.md`, section 6.1) cover both modes, the inbox and the voice path.
- Two speech-to-text modes, switchable from the tray menu ("Transcription") or the
  settings window: CPU (faster-whisper int8 with VAD, leaves the GPU free for other work)
  and GPU (the fastest). Each mode keeps its own Whisper model. A mode that is not
  installed is installed by the companion after asking, with the download size, inside the
  engine's own environment only. `make stt-check` and `make stt-install MODE=cpu|gpu` do
  the same by hand, and `make setup` and `make configure` ask for the default mode.
- `make docs-screenshots` also renders the Assistant settings tab and the assistant window,
  and the README, `docs/usage.md`, `docs/configuration.md`, `docs/architecture.md` and
  `docs/troubleshooting.md` describe the transcription modes and the voice assistant.
- External TTS provider: a generic "Custom provider (HTTP)" voice engine for any service that follows the new
  `voicemate-tts` protocol (docs/tts-provider-protocol.md). Engine flags `--tts-engine external`,
  `--tts-url`, `--tts-external-voice` and `--tts-options`; the token comes from `VOICEMATE_TTS_TOKEN`.
  The companion has a Voice tab with the address, a voice picker loaded from the provider, a masked
  token and a Test voice button. `make tts-provider-check URL=...` checks a provider, and
  `make tts-provider-example` runs a test-tone reference provider.
- The assistant speaks: the companion sends the `<voice>` block of each reply, sentence by sentence, to the
  engine's new `/tts` endpoint (the `voicemate-tts` protocol, served by the engine with its own voice engine:
  Kokoro, or the custom provider it relays) and plays the audio while the rest is synthesized. Settings >
  Assistant has "Speak the replies" and an "Assistant voice" picker loaded from the engine, with a Test button
  (`speak_replies` and `voice` in `[assistant]`, also changeable by voice). "Stop talking", a new prompt, a
  dictation hotkey and a mode change silence it at once. When the engine cannot speak, the replies stay text
  only with one "No voice available" notice, and the short notices use the Windows voice.
- The voice pickers (Voice and Assistant tabs) are sorted by name with the default first, and typing part of a
  name filters them, for providers with hundreds of voices.
- Engine flags `--stt-mode {auto,cpu,gpu}` and `--cpu-threads`. `/health` reports where
  the engine transcribes (`stt`), and the status window shows it.

### Changed

- A microphone open that the audio system refuses is tried again by itself (3 attempts, 0.4 s
  apart) before "Microphone unavailable" shows. On WSLg the first open sometimes fails at once
  and the next one works, which used to cost a second key press.
- The status window has an "Assistant" button, shown even while the assistant is off (its window
  then says how to turn it on), with the window hotkey in its tooltip: people could not tell the
  assistant existed.
- The CPU path decodes greedily with a VAD filter, and the `gpu` mode decodes greedily:
  in the benchmark (docs/configuration.md) beam search cost time for little or no
  accuracy. `auto` keeps the configured beam.
- AMD in WSL2 goes straight to openai-whisper (ROCm): the whisper.cpp build there runs on
  the CPU. An explicit `--stt-strategy whispercpp` still uses it.
- The Claude flow now defaults to `claude-haiku-5-5` (it was `claude-haiku-4-5`), and the
  suggested larger model is `claude-sonnet-5-5`. Haiku 5.5 accepts the `effort` level, so the
  runtime now sends it.
- Models that reject disabled thinking (Sonnet 5.5, Opus 5.5, Fable, and Haiku 5.5 or Opus 5 at
  effort `xhigh` or `max`) keep thinking on instead of failing at startup, so
  `--claude-model claude-sonnet-5-5` works. Short aliases (`haiku`, `sonnet`, `opus`, `fable`)
  follow the rules of the current generation.

## [0.1.0] - 2026-10-02

First public release. It bundles everything built since the first working
transcription in March 2026: the dictation engine, the experimental Linux, WSL2 and
AMD support, the Windows tray companion with its installer, five languages and the
experimental voice conversation with Claude.

### Added

#### Dictation engine

- Toggle hotkey dictation: press `Ctrl+Alt+V`, speak, press it again, and the
  transcription lands on the clipboard, ready to paste anywhere.
- Local speech-to-text with Whisper, no cloud service involved. The default model is
  `large-v3-turbo`; `--model` picks another one (`make run-large`, `make run-turbo`).
- Pinned transcription language (`--transcription-language`: `auto`, `pt`, `en`,
  `es`, `fr`, `de`, `it`, `ja`, `ru`, `zh`), derived from `--output-lang` by default,
  so short utterances are not misdetected as another language.
- The microphone opens only while recording, on a background thread: a missing or
  stuck microphone is reported ("microphone unavailable") within about 6 seconds and
  the session returns to idle instead of blocking the hotkey.
- Sound cues for start, time-limit warning, transcription done and error, generated
  in code so they work on every platform.
- Maximum recording length with a warning at 80% (default 10 minutes,
  `--max-recording-seconds`) and a watchdog that restarts a hung process
  (`--no-watchdog`, `--watchdog-timeout`).
- Mouse side-button trigger as an alternative to the keyboard (`--input-method mouse`,
  `--mouse-button`).
- The engine can run as a headless daemon for the companion app (`make run-engine`,
  `--supervised`, `--api`, `--api-token`): a local HTTP API (version 2) that binds
  before the model loads, streams recording phases and results, and keeps each result
  until a client confirms it.

#### Platforms and GPUs

- Platform layer that detects Windows, Linux X11, Linux Wayland and WSL2 and picks the
  matching hotkey trigger and clipboard (`--platform`, `--trigger`, `--daemon-port`,
  default port 47821). The original Windows path is unchanged.
- **Experimental:** native Linux support (X11 through `pynput`, Wayland through `evdev`,
  which needs the user in the `input` group) and WSL2 support, where the engine runs
  entirely inside WSL and Windows triggers it through the local daemon.
- **Experimental:** AMD GPU support. Validated on a single card (Radeon RX 9070 XT)
  under WSL2 with ROCm 7.2. NVIDIA on WSL2 has not been tested.
- `make setup` and `make configure` detect NVIDIA, AMD or CPU, install the matching
  PyTorch build (CUDA `cu128`, AMD's ROCm wheels from `repo.radeon.com` on Linux/WSL2,
  or CPU), ask which modules to install and remember the choices in
  `~/.config/voicemate/config.toml`.
- Three speech-to-text backends: `faster-whisper` (NVIDIA and CPU), `whisper.cpp` with
  Vulkan (kept warm as a server by default, with Silero VAD) and `openai-whisper` on
  ROCm PyTorch (extra `whisper-gpu`). On AMD a layered chain uses the first backend that
  works: CTranslate2-ROCm (opt-in build), whisper.cpp, openai-whisper, then CPU. On WSL2
  openai-whisper comes before whisper.cpp, because Vulkan there only reaches a software
  renderer. Flags: `--gpu-backend`, `--whisper-backend`, `--stt-strategy`,
  `--whispercpp-mode`.
- A warning when a transcription takes more than 3 times the length of the audio (and
  at least 5 seconds), which points at a silent fallback to the CPU.
- `make doctor`: environment diagnostics (WSLg microphone and audio, `input` group,
  whisper.cpp, Vulkan device, PyTorch GPU, Claude CLI, clipboard) with a suggested fix
  for each failed check.
- Windows hotkey scripts for the WSL2 daemon (PowerShell, recommended, and AutoHotkey
  v2), with the Windows clipboard written on the Windows side and Windows
  notifications for important warnings.
- systemd user unit (`scripts/systemd/voicemate.service`) to start the engine with WSL;
  `make setup` offers to install it.

#### Companion app (Windows tray)

- Tray app (PySide6) that starts and supervises the engine inside WSL2, or attaches to
  one that is already running (for example the systemd service). It replaces the
  PowerShell and AutoHotkey scripts on Windows.
- Global hotkeys registered by the companion, changeable in Settings > **Hotkeys**; a
  chord taken by another program shows "Used by another app".
- Verified clipboard delivery: every transcription is written to the Windows
  clipboard, checked and confirmed to the engine. A transcription that could not be
  copied stays in the **Not copied** list (tray menu and status window), which
  survives restarts and crashes until you copy it or clear the list.
- Tray icon with a small corner badge for recording, transcribing and ready; a status
  window; recent results; mute sounds; engine restart and logs; **Restart WSL...**;
  **Quit VoiceMate**.
- Sound cues from built-in presets or your own WAV files, with volume control
  (Settings > **Sounds**).
- **Dictation language** (Settings > **General**): **Same as the interface** (the
  default), **Detect automatically** or one of Portuguese, English, Spanish, Russian,
  Chinese, French, German, Italian and Japanese. For the engine VoiceMate starts, it
  sets `--transcription-language` and `--output-lang` (so also the language of Claude's
  spoken answers, except with **Detect automatically**, where Claude keeps answering in
  the interface language) and restarts the engine. An engine VoiceMate only connects to
  keeps its own flags.
- Automatic recovery: the engine is restarted with backoff after a crash, and WSL is
  restarted when WSLg audio stops, following the **Restart WSL when audio fails**
  setting (**Automatically**, **Ask first** or **Never**).
- Start at sign-in, single instance, Windows taskbar identity with a jump list, the tray
  icon kept visible on the Windows 11 taskbar unless you hid it in Windows, a one-time
  tip on how to pin VoiceMate to the taskbar, and `VoiceMate.exe --command quit`.
- Optional companion on Linux (`poetry install --extras ui`, `make run-tray`): the
  engine keeps its own hotkeys, and the status window is the main window when there is
  no system tray.
- VoiceMate mark: a figure with raised arms drawn as a voice wave and a coral head;
  the tray glyph is minimal and the state shows only as a badge.

#### Installer

- Per-user Windows installer, `VoiceMate-Setup-<version>.exe` (Inno Setup): no
  administrator prompt, installs into `%LOCALAPPDATA%\Programs\VoiceMate`, adds a Start
  menu entry, and offers a desktop shortcut and start at sign-in (the latter on the first
  install only). The installer needs Windows 10 version 1809 or newer, 64-bit; the
  engine it drives needs WSL2 with WSLg (Windows 10 build 19044 or newer, or Windows 11).
- Upgrading closes a running VoiceMate, and the engine it started, before installing.
- Uninstalling removes the logs, the rendered sounds and the Not copied list, and keeps
  the settings.

#### Languages

- Engine, companion, Windows hotkey script and installer in English, Portuguese
  (Brazil), Spanish, Russian and Simplified Chinese (gettext catalogs `en`, `pt_BR`,
  `es`, `ru`, `zh_CN`).
- The engine follows `VOICEMATE_LANG` (default `pt-BR`); the companion follows its
  **Language** setting or the Windows display language, and offers to restart when the
  language changes.
- README in the same five languages.

#### Voice conversation with Claude and TTS (experimental)

- Second hotkey, `Ctrl+Alt+A`: your speech goes to Claude through the Claude Agent SDK
  and the locally installed, signed-in Claude Code CLI. The transcription is copied
  first and the answer next, so the Windows clipboard history (`Win+V`) shows both.
- Multi-turn conversation while the engine runs; the hotkey that stops the recording
  decides where the text goes; pressing a hotkey while Claude answers or speaks cancels
  and starts a new recording, keeping the conversation.
- Spoken answers through a pluggable text-to-speech layer, streamed sentence by
  sentence: Kokoro (light, runs on the CPU, extra `kokoro`), OmniVoice (the engine used
  when none is saved, extra `tts`) and VoxCPM2 (voice designed from a text description,
  extra `voxcpm`). `make setup` lets you pick Kokoro or OmniVoice.
- `--output-lang` sets the language Claude answers in (default `pt-BR`); further flags
  for the model, effort, thinking, timeout, system prompt and TTS voice
  (`make run ARGS="--help"` lists them).

#### Developer tooling

- Poetry project pinned to Python 3.12, with optional extras (`claude`, `tts`, `kokoro`,
  `voxcpm`, `whisper-gpu`, `linux`, `ui`, `all`).
- Makefile for every workflow: setup, `format`, `lint` (Ruff and strict Mypy), `test`
  (pytest), `all`, the run targets, `i18n-*` and `companion-*` (venv, lint, test, build,
  installer), `docs-screenshots` (the README images) and `release-check` (release
  consistency before tagging).
- `make stt-eval`: word error rate and split-word gate per speech-to-text backend
  against local samples, with a saved baseline.
- `.gitattributes` enforces LF line endings.
- Documentation: installation, usage, configuration, troubleshooting and architecture
  guides, `docs/wsl2.md`, `docs/companion-app.md` (architecture and API v2 protocol),
  `docs/brand.md`, `docs/releasing.md` and `CONTRIBUTING.md`.
- README overhaul in five languages, with real screenshots and diagrams.
- GitHub Actions workflows: continuous integration (companion lint and tests on
  Windows, engine lint and tests on Ubuntu), and a release workflow that builds the
  Windows installer on `windows-latest` from a version tag and attaches it with its
  SHA-256 checksum (`VoiceMate-Setup-<version>.exe.sha256`) and the release notes in
  five languages (`VoiceMate-<version>-release-notes.<lang>.md`).

### Changed

Changes made during development that matter to anyone who ran VoiceMate from an older
checkout of `main`:

- Default text-to-speech engine: OmniVoice instead of VoxCPM2 (VoxCPM2 moved to the
  `voxcpm` extra). The engine chosen in `make setup` wins over the default.
- Default voice seed mode for TTS: `off` (a fixed voice from the description) instead
  of `auto`.
- Default Claude model: `claude-haiku-4-5` instead of `claude-sonnet-4-6`, for a faster
  first answer.
- whisper.cpp runs as a warm server by default (`--whispercpp-mode server`).
- Source code and messages are English; Portuguese and the other languages come only
  from the translation catalogs.
- The engine runs one transcription at a time, and the first transcription waits for
  the background warmup.
- Code layout: `app/core/` (always installed) and `app/features/` (opt-in), with the
  command line in `app/cli/`; Claude and TTS are optional extras.

### Fixed

Bugs found while building this release; they only affected earlier checkouts of
`main`:

- Global hotkeys that stopped working under heavy CPU load, because Windows silently
  removes slow low-level hooks: the listeners are now reinstalled every 60 seconds
  (`--listener-refresh-seconds`, `--no-listener-refresh`).
- TTS quality that degraded after a few answers, and the voice description being read
  aloud in voice cloning mode.
- A Claude timeout now cancels the request and returns to idle.
- WSL2: a deadlock when stopping a recording, a missing `clip.exe` when Windows paths
  are off in `/etc/wsl.conf`, unusably slow transcription when Vulkan fell back to
  software, crackling TTS over WSLg, a slow first transcription, and words split in two
  in whisper.cpp output.
- Fresh clones always printed English because the translation catalogs were never
  compiled; `make setup`, every run target and the systemd unit now compile them.
- The engine stops cleanly on SIGTERM, also while the model is loading, and never hangs
  on stop when its API is gone.
- The Windows hotkey script recovers from daemon restarts, tells an offline daemon from
  a busy or failed one, and retries clipboard delivery instead of dropping a
  transcription.

### Security

- The local HTTP API listens on loopback only and rejects requests with an `Origin`
  header, a non-loopback `Host` header, and `GET /trigger`, so a web page cannot trigger
  a recording through the port WSL2 forwards to Windows.
- With `--api-token` (the companion's mode) every route except `/health` requires a
  bearer token stored in `~/.config/voicemate/api-token`, created with mode 0600.
- `make setup` checks the whisper.cpp binary, model and VAD downloads against pinned
  SHA-256 hashes.

### Notes for people upgrading from a source checkout

- `poetry install` alone no longer installs a GPU build of PyTorch: run `make setup`
  once (or `make configure` to change the choices), which installs the right build for
  your GPU.
- The engine needs Python 3.12 (`>=3.12,<3.13`, because AMD's ROCm wheels are cp312
  only); recreate the virtual environment if it was built on 3.13.
- Claude and TTS are optional extras: a bare `poetry install` gives dictation only.
  `make setup` installs what you pick; the `ui` extra (companion) is not part of `all`.
- Imports from `app.services` no longer exist; the code lives in `app.core` and
  `app.features`.
- The default TTS engine is now OmniVoice and the default voice seed mode is `off`: use
  `poetry install --extras voxcpm` and `--tts-engine voxcpm`, and
  `--tts-voice-seed-mode auto`, for the old behavior.
- The default Claude model is now `claude-haiku-4-5`, which ignores `--claude-effort`:
  pass `--claude-model claude-sonnet-4-6` to keep the previous model.
- The legacy PowerShell and AutoHotkey scripts only work with an engine started without
  a token (`make run`), not with `make run-engine`. With the companion on Windows, close
  them and remove their shortcuts from `shell:startup`: the companion owns `Ctrl+Alt+V`
  and `Ctrl+Alt+A`.
- If the companion reports "The engine is older than this app", update the checkout in
  WSL (`git pull`) and use **Restart engine**.

### Project history before 0.1.0

Development happened on `main` and in seven pull requests before this first release:

- [#1](https://github.com/nano-br/voice-mate/pull/1): reinstall the hotkey listeners
  periodically (Windows hook removal).
- [#2](https://github.com/nano-br/voice-mate/pull/2): MIT license, package metadata,
  English and Portuguese READMEs.
- [#3](https://github.com/nano-br/voice-mate/pull/3): voice to Claude flow
  (`Ctrl+Alt+A`).
- [#4](https://github.com/nano-br/voice-mate/pull/4): core/features modules, pluggable
  TTS with VoxCPM2, first i18n.
- [#5](https://github.com/nano-br/voice-mate/pull/5): experimental Linux, WSL2 and AMD
  support.
- [#6](https://github.com/nano-br/voice-mate/pull/6): Windows tray companion, installer,
  Russian and Simplified Chinese.
- [#7](https://github.com/nano-br/voice-mate/pull/7): choose the dictation language in
  the companion.

[Unreleased]: https://github.com/nano-br/voice-mate/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/nano-br/voice-mate/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nano-br/voice-mate/releases/tag/v0.1.0
