# Troubleshooting

Purpose: how to diagnose VoiceMate problems, with the exact messages the app shows and what to do about each one. Back to the [README](../README.md).

## First steps

1. **Read the logs.** Tray menu > **Engine** > **Open logs** opens the folder with `companion.log` and `engine.log` (Windows `%LOCALAPPDATA%\VoiceMate\logs`, Linux `~/.local/state/voicemate/logs`). `engine.log` holds everything the engine printed.
2. **Run `make doctor`** in the engine folder (inside WSL for the companion). It never stops at the first problem: each check prints ✓ or ✗, and each failed check prints a `fix:` line with the command to run.
3. **Restart the engine:** **Engine** > **Restart engine**. It also resets the supervisor after "VoiceMate stopped retrying".

### What `make doctor` checks

In this order: the detected platform; on WSL2 only, the WSLg PulseAudio socket, `PULSE_SERVER`, the ALSA to PulseAudio plugin, the native clipboard tools (`wl-copy`, `xclip`) and WSL interop for `clip.exe`; the audio library, the input device and the output device; the hotkey trigger (access to `/dev/input` for `evdev`, `DISPLAY` for `pynput`, the HTTP daemon on WSL2, or the Windows hooks); the whisper.cpp binary, model and VAD; the Vulkan device (Linux and WSL2); the Claude CLI (`node` and `claude`, skipped when the saved flow is `clipboard`); `espeak-ng` when Kokoro is installed; PyTorch with GPU.

## Companion messages

| Message (notification title) | Meaning | What to do |
|---|---|---|
| "VoiceMate is starting" | A hotkey was pressed while the engine still loads the model (10 to 60 seconds, much longer on the very first start, which downloads the model) | Wait and press again |
| "Engine folder not found" | No engine checkout in the folders the companion searches | Clone the engine into one of them ([installation.md](installation.md#2-install-the-engine)) or set "Engine folder:" in Settings > **General** |
| "Could not start the engine" | `wsl.exe` or `make run-engine` failed | Check `engine.log`; check that `make` and `poetry` work in a login shell (`wsl -e bash -lc "make --version && poetry --version"`) |
| "VoiceMate stopped retrying" | The engine failed too often (more than 5 restarts in 15 minutes, 3 start timeouts in a row, or more than 3 WSL restarts in 1 hour) | Read `engine.log`, fix the cause, then use **Restart engine**. On the very first start a slow model download can use up the 240 seconds of an engine start three times in a row: run `make run` once in the engine folder (inside WSL), stop it with `Ctrl+C` when it has loaded, then use **Restart engine** |
| "Engine is outdated" / "The engine is older than this app. Restart or update it." | The engine checkout is older than the companion | Update it (`git pull`, then `make setup`), then **Restart engine** |
| "Engine run by systemd" | The `voicemate` systemd service runs the engine, so the companion attaches to it | Keep it, or run `systemctl --user disable --now voicemate` in WSL to let VoiceMate manage the engine |
| "Engine access denied" | The engine refused the API token | VoiceMate reads `~/.config/voicemate/api-token` again every 30 seconds; if it persists, restart the engine |
| "Engine unreachable" / "Engine busy" | The engine did not answer a hotkey (in time) | VoiceMate keeps reconnecting; check `engine.log` if it lasts |
| "Hotkey unavailable" / "Used by another app" (Hotkeys tab) | Another program holds the chord. Often the old VoiceMate hotkey script | Close the script and remove it from `shell:startup`, or choose other hotkeys |
| "No microphone" | Windows has no microphone | Connect one |
| "Microphone unavailable" | The engine could not open the microphone | On Windows: allow desktop apps to use the microphone in the Windows privacy settings, and close any app that holds it exclusively. If WSL audio is stuck, restart WSL |
| "WSL audio stopped" | WSLg PulseAudio does not answer | See [WSL audio](#wsl-audio-and-the-microphone) |
| "Nothing transcribed" / "Nothing recorded" | No speech was detected, or the recording captured no audio | Check the microphone and the input level |
| "Slow transcription" | Transcription runs much slower than the audio, usually without a GPU | `make doctor` (PyTorch with GPU, Vulkan device); see [wsl2.md](wsl2.md#speech-to-text-performance-on-wsl2) |
| "Transcription failed" | The speech-to-text backend raised an error | Read `engine.log` |
| "Claude did not answer" | The request to Claude failed | Read `engine.log`; check `claude` works in WSL (`claude -p "ping"`) |
| "Not copied to the clipboard" | Another app kept the clipboard locked | The text is in **Not copied**: click it there to copy it |
| "Copy failed" | Copying from Recent or Not copied failed | Try again |
| "Settings reset" | `companion.toml` could not be read; VoiceMate started with defaults and kept the old file as `companion.toml.broken-<timestamp>` | Compare and fix the old file, or keep the defaults |
| '"Not copied" list reset' | `pending.json` could not be read; the old file (with its texts) was kept as `pending.json.broken-<timestamp>` | Recover texts from the old file if you need them |

## Transcription mode

| Message | Meaning | What to do |
|---|---|---|
| "Install the CPU mode?" / "Install the GPU mode?" | The mode you chose is not installed in the engine's environment | **Install** downloads it (the size is in the question) and shows the output. Cancel keeps the current mode |
| "The installation failed. The details are below; the engine keeps the current mode." | `make stt-install` exited with an error | Read the output in the window, fix the cause (network, disk space, ROCm), try again, or run `make stt-install MODE=gpu` in the engine folder |
| "No GPU mode on this computer" | The check found no graphics card VoiceMate can use | Use the CPU mode |
| "The GPU mode is not working. Install it from the Transcription menu." (status window) | The GPU mode fell back to the CPU | Tray menu > "Transcription" > "GPU (fastest)" offers the install; `make doctor` shows the GPU checks |
| "Transcription mode change under way" | A check or an install is still running | Wait for it to finish, then try again |
| "Could not change the transcription mode" | The engine could not restart with the new mode | Read `engine.log` |

## Voice assistant (experimental)

| Symptom | Cause | What to do |
|---|---|---|
| "Claude Code was not found" | The `claude` command is not on the `PATH` of Windows | Install Claude Code, or set `VOICEMATE_CLAUDE_CLI` to its full path, then press **Restart** in the assistant window |
| "Claude Code is not signed in" | No Claude login on this computer | Run `claude auth login` in a terminal, then **Restart**. The login of Claude Desktop does not count |
| "The assistant could not start" or "The assistant stopped" | The process failed or ended | Open `companion.log` (tray menu > **Engine** > **Open logs**), fix the cause, then **Restart** |
| "The assistant could not be set up" | VoiceMate could not install the skill or write `~/.voicemate/assistant.json` | Check that your user folder and `~/.claude` are writable; the log has the details |
| No "Open on claude.ai" link, nothing on the phone | Remote Control could not be turned on. The assistant keeps working on this computer | Restart the conversation: Settings > **Assistant**, set "Assistant mode:" to off, apply, then back on. `companion.log` says what the request answered. The request is undocumented and can change between Claude Code versions (see the [research](research/claude-code-client.md#61-a-voicemate-owned-session-in-the-desktop-tested-works)) |
| The assistant answers in the wrong language, or without a name | The skill could not read `~/.voicemate/assistant.json` and says "VoiceMate's assistant settings were not found" | Restart the conversation (Settings > **Assistant**, off, apply, then back on): VoiceMate writes the file again when the conversation starts. Check that the file is in your user folder (`~/.voicemate/`) |
| There is no "Assistant" entry in the tray menu | The assistant is off | Settings > **Assistant** > "Assistant mode:" > "VoiceMate's own conversation" |
| "More than one conversation matches ..." or "No conversation matches ..." | A typed command could not pick one title | Type more of the title, or switch off "Understand voice commands in typed text" |
| A personality file is refused | It is not an existing `.md` file | Pick a Markdown file with "Browse..." |
| "No Claude Desktop conversation is chosen" | The mode is "A Claude Desktop conversation" and "Desktop conversation:" is empty | Type the exact title from the Claude Desktop sidebar in Settings > **Assistant** |
| "The assistant could not be set up" in the Desktop mode, and the log says "no Claude Desktop conversation titled ..." | No Desktop conversation with a transcript has that exact title | Check the title letter by letter in the Claude Desktop sidebar; a conversation that never had a message has no transcript yet |
| "The assistant could not start" in the Desktop mode, and the log says `window_missing`, `conversation_missing` or `ambiguous` | Claude Desktop is not open, the conversation is not in its sidebar, or two have that title | Open Claude Desktop, rename one of two equal titles, then press **Restart** |
| The log says `locked` or `focus_lost`, and a message did not arrive in the Desktop | The screen was locked, or another window took the focus while VoiceMate was typing. Nothing was typed | Unlock the screen, leave the keyboard alone for the 2 seconds a message takes, and send it again |
| A draft you were typing in a Desktop conversation disappeared | A message from the assistant clears the draft of the conversation it writes to | Do not type in the conversation the assistant uses (the Desktop mode), or in one it is about to write to |
| After a Claude Desktop update the Desktop mode stops finding its controls | The update changed the window, so the profile no longer matches | Settings > **Assistant** > **Check now** for a newer profile; if there is none, see [desktop-profile.md](desktop-profile.md#writing-a-profile-for-a-new-desktop-version) |
| "Problem: ..." under the Claude Desktop profile | The imported or downloaded profile was refused (the reason names the field) | Fix the file and **Import...** it again, or press **Use default** |
| The assistant cannot browse, open pull requests or use other Desktop-only tools | In "VoiceMate's own conversation" it only has the `voicemate` tools | Ask it to hand the task to a Desktop conversation, or switch to the Desktop mode |
| The assistant is silent, and the window says "No voice available" | The engine cannot speak: it is stopped, its voice engine is not Kokoro or a custom provider, or the provider is down | Check Settings > **Voice** and the engine's voice engine ([protocol](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)); "Speak the replies" must be on. The replies stay readable in the window |
| An answer from another conversation never arrived | Only conversations the assistant wrote to or started are watched, and a watch ends after one hour | Ask the assistant what is pending. With "Just keep it" or "Only play a sound" the answer waits in **Answers waiting**, not in the conversation |
| The tray icon has a blue badge | Answers from other conversations are waiting | Use "Hear what's ready" (tray menu, `Ctrl+Alt+R`) or open the window |
| A hotkey of the assistant says "Used by another app" | `Ctrl+Alt+S`, `Ctrl+Alt+W` or `Ctrl+Alt+R` is taken | Settings > **Hotkeys**: change it or clear it |
| The assistant refuses to change a setting by voice | It is not in the catalog (hotkeys, files, folders and the engine are left out), or the value is not an allowed one | Change it in Settings. The list is in [usage.md](usage.md#change-voicemate-by-voice) |

## WSL audio and the microphone

- Opening the microphone never blocks a hotkey: it runs in the background. If it does not open within 6 seconds, the engine plays the error cue, returns to idle and reports "Microphone unavailable".
- On WSL2 the engine probes WSLg PulseAudio (`pactl info`) every 20 seconds and reports the audio as working, not working or unknown ("Audio:" in the status window).
- When Windows loses its microphone, WSLg PulseAudio often gets stuck for good, even after the microphone comes back (`pactl info` gives `Connection failure: Timeout`). Only `wsl --shutdown` brings it back.

The companion handles this with the "Restart WSL when audio fails:" setting:

| Setting | Behavior |
|---|---|
| "Automatically" (default) | Restarts WSL by itself, unless other WSL distros run: `wsl --shutdown` stops all of them, Docker included, so then it asks first |
| "Ask first" | Shows "WSL needs a restart" and the menu item "Restart WSL now..." |
| "Never" | Does nothing; restart by hand with **Engine** > **Restart WSL...** |

A WSL restart is wanted when the audio is down on two probes in a row, or when the microphone fails while Windows has a capture device and the audio is not working. It never happens while Windows has no microphone: the companion waits for one (it checks every 3 seconds). Before the restart it waits up to 30 seconds for the engine to be idle, then stops the engine, runs `wsl --shutdown` and starts the engine again.

Without the companion: run `wsl --shutdown` in PowerShell, then `make run` again.

## Hotkeys

- **Windows with the companion:** the companion registers `Ctrl+Alt+V` and `Ctrl+Alt+A`. If a chord shows "Used by another app", another program owns it.
- **Windows native engine (`make run`):** Windows can silently remove the low-level keyboard hook when the system is under heavy load. The engine reinstalls it every 60 seconds (`--listener-refresh-seconds`), and a watchdog restarts a hung engine (`--watchdog-timeout`).
- **Wayland:** the `evdev` trigger needs your user in the `input` group (`sudo usermod -aG input $USER`, then sign in again).
- **X11:** the `pynput` trigger needs `DISPLAY`.

## Engine problems

The engine messages quoted in this guide are English. The engine prints them in the language of `--output-lang`, whose default is Portuguese (`pt-BR`), or of `VOICEMATE_LANG`, which wins. An engine you start with a plain `make run` therefore logs in Portuguese: add `ARGS="--output-lang en"` (or set `VOICEMATE_LANG=en` in the shell that runs it) to read the English text. The companion passes the flag itself, from "Dictation language:" in Settings.

| Symptom | Cause | Fix |
|---|---|---|
| "Could not open the HTTP API on 127.0.0.1:{port}" in the engine log | The port is taken, often by another engine | Stop the other engine, or choose another port (`--daemon-port`, and `daemon_port` in `companion.toml`) |
| "Claude flow disabled: extra 'claude' not installed." | The `claude` extra is missing | `poetry install --extras claude`, or `make configure` |
| "Failed to start Claude (run `claude login`)" | The Claude CLI is missing or signed out | Install it and sign in ([installation.md](installation.md#claude-code-cli-for-the-claude-flow)) |
| "TTS disabled: engine '{engine}' packages not installed." | The extra of the TTS engine is missing | Install the extra named in the next log line, or run with `--no-tts` |
| English speech is transcribed wrongly (as if it were Portuguese) | An engine started by hand pins Whisper to Portuguese by default | `make run ARGS="--output-lang en"` or `--transcription-language en`. With the companion, set "Dictation language:" in Settings > **General** |
| Transcription is 10 to 50 times slower than expected | The backend fell back to the CPU | `make doctor` (PyTorch with GPU); on AMD, `rocminfo` must list the GPU |
| CTranslate2-ROCm failed to build | Incomplete ROCm development packages | `make doctor`; install the ROCm HIP SDK; `make configure` tries again |
| The interface language is not the one you chose | The language changes after a restart | Answer "Restart now" in "Restart VoiceMate?" |

## Old hotkey scripts (without the companion)

The PowerShell script `scripts/windows/voicemate-hotkeys.ps1` prints "daemon offline" when nothing answers on the port: start the engine in WSL with `make run`, then press the hotkey again. These scripts only work with an engine started without a token (`make run`), never with `make run-engine`.
