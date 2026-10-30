# Configuration reference

Purpose: every engine flag, settings key, environment variable and file location of VoiceMate. Back to the [README](../README.md).

## Where settings come from

| Part | Source | Precedence |
|---|---|---|
| Engine | Command-line flags of `voice-mate` (`make run ARGS="..."`) and `~/.config/voicemate/config.toml` (written by `make setup` and `make configure`) | Flag, then `config.toml`, then detection or the built-in default |
| Companion | `companion.toml`, edited through Settings | The file; invalid values fall back to the default |

Flags reach the engine through the Makefile with `ARGS`, for example `make run ARGS="--model medium --no-tts"`. `make` does not forward words placed after the target, so always use `ARGS`.

## Engine flags

### Speech-to-text and input

| Flag | Default | Meaning |
|---|---|---|
| `--model {tiny,base,small,medium,large-v2,large-v3,large-v3-turbo}` | `large-v3-turbo` | Whisper model |
| `--hotkey` | `ctrl+alt+v` | Hotkey of the clipboard flow |
| `--cpu` | off | Force the CPU (int8). Wins over every GPU setting |
| `--stt-mode {auto,cpu,gpu}` | saved choice, else `auto` | Where to transcribe. `cpu`: faster-whisper int8 on the processor, greedy decoding with VAD, the GPU stays free for other work. `gpu`: the GPU backend with greedy decoding, falling back to the CPU (with an install hint) when it does not work. `auto`: the GPU when its backend works, otherwise the CPU. See [Speech-to-text modes](#speech-to-text-modes) |
| `--cpu-threads` | `0` (half the cores, at most 8) | CPU threads of faster-whisper on the CPU |
| `--gpu-backend {auto,nvidia,amd,cpu}` | saved choice, else detected | GPU vendor; `auto` detects the card |
| `--whisper-backend {faster-whisper,whispercpp,openai-whisper}` | saved choice, else per vendor | Speech-to-text engine |
| `--stt-strategy {auto,faster-whisper-rocm,whispercpp,openai-whisper}` | saved choice, else `auto` | AMD only: the first backend of the fallback chain |
| `--whispercpp-mode {server,cli}` | `server` | `server` keeps the whisper.cpp model loaded; `cli` reloads it for every recording (slower) |
| `--transcription-language {auto,pt,en,es,fr,de,it,ja,ru,zh}` | derived from `--output-lang` | Language pinned for transcription. `auto` detects it per recording (less stable on short recordings) |
| `--input-method {keyboard,mouse}` | `keyboard` | `mouse` works on Windows only (`keyboard-hooks` trigger) and only for the clipboard flow; it disables the Claude flow |
| `--mouse-button` | `x` | Mouse button for `--input-method mouse` (`x` is a side button) |
| `--max-recording-seconds` | `600` | Recording limit. A warning plays at 80 %; at the limit the recording stops and is transcribed |
| `--output-lang` | `pt-BR` | BCP-47 code. The language of Claude's answers, the default transcription language (`pt-BR` gives `pt`, `en` gives `en`, `zh-CN` gives `zh`) and the default language of the engine messages. The default pins Whisper to Portuguese: pass `--output-lang en` (or `--transcription-language en`) when you dictate in English. The companion passes it for the engine it starts, from the "Dictation language:" setting |

### Platform, trigger and API

| Flag | Default | Meaning |
|---|---|---|
| `--platform {windows,linux-x11,linux-wayland,wsl2}` | saved choice, else detected | Runtime environment |
| `--trigger {keyboard-hooks,pynput,evdev,socket}` | per platform: `windows` uses `keyboard-hooks`, `linux-x11` uses `pynput`, `linux-wayland` uses `evdev`, `wsl2` uses `socket` | How hotkeys reach the engine. `socket` means the HTTP API is the trigger |
| `--daemon-port` | saved choice, else `47821` | Port of the local HTTP API |
| `--api` | off (always on with `--trigger socket`) | Serve the HTTP API next to the native hotkey listener |
| `--api-token` | off | Require `Authorization: Bearer <token>` on every route except `/health` (token file below) |
| `--supervised` | off | Exit cleanly when stdin reaches end of file (the companion holds stdin open) |
| `--no-watchdog` | watchdog on | Disable the watchdog that restarts a hung engine |
| `--watchdog-timeout` | `120` | Watchdog timeout in seconds |
| `--no-listener-refresh` | refresh on | Windows only: disable the periodic reinstall of the keyboard hook |
| `--listener-refresh-seconds` | `60` | Windows only: interval of that reinstall |

`make run-engine` runs `voice-mate --supervised --api --api-token`; it is what the companion starts.

### Claude flow

| Flag | Default | Meaning |
|---|---|---|
| `--claude-chat-hotkey` | `ctrl+alt+a` | Hotkey of the Claude flow |
| `--no-claude-chat` | off | Disable the Claude flow (clipboard only) |
| `--claude-system-prompt` | built-in prompt | Replace the system prompt |
| `--claude-no-system-prompt` | off | Send no system prompt. Wins over `--claude-system-prompt` |
| `--claude-max-turns` | `50` | Turns per conversation |
| `--claude-model` | `claude-haiku-5-5` | Claude model |
| `--claude-effort {low,medium,high,xhigh,max}` | `low` | Effort level. Ignored by Haiku 4.5 and older |
| `--claude-enable-thinking` | off | Extended thinking. Sonnet 5.5, Opus 5.5 and Fable always think, and so do Haiku 5.5 and Opus 5 at effort `xhigh` or `max` |
| `--claude-timeout-seconds` | `120.0` | Timeout of each turn |

### Text-to-speech (Claude flow)

| Flag | Default | Meaning |
|---|---|---|
| `--no-tts` | off | No spoken answer; the answer still goes to the clipboard |
| `--tts-engine {<default>,kokoro,voxcpm,external,none}` | saved choice, else the default engine | TTS engine. `external` is a service you run ([protocol](tts-provider-protocol.md)). `none` disables TTS |
| `--tts-url` | `http://127.0.0.1:8765` | `external` only: base URL of the provider |
| `--tts-external-voice` | provider default | `external` only: voice id (the companion's voice picker fills it) |
| `--tts-options` | none | `external` only: a JSON object passed to the provider as `options` (speed, style). Anything else is ignored with a warning |
| `--tts-kokoro-voice` | `pf_dora` | Kokoro voice. Portuguese voices: `pf_dora` (female), `pm_alex` and `pm_santa` (male) |
| `--tts-voice` | a Portuguese description of an elderly Brazilian woman's voice | VoxCPM2 only: text description of the voice, used when there is no voice seed |
| `--tts-cfg-value` | `2.0` | VoxCPM2 `cfg_value` (1.0 to 3.0) |
| `--tts-inference-timesteps` | `10` | VoxCPM2 inference steps (4 to 30) |
| `--tts-device {auto,cuda,cpu,mps}` | `auto` | TTS device |
| `--tts-save-dir` | none | Save every generated audio file to this folder |
| `--tts-no-streaming` | off | Generate in one shot instead of streaming (debugging) |
| `--tts-voice-seed-mode {auto,fixed,off}` | `off` | How the default engine and VoxCPM2 choose the voice (see [usage.md](usage.md#voice-seed-modes)) |
| `--tts-voice-seed-path`, `--tts-voice-seed-text` | none | Reference WAV and its exact text, required by `fixed` |
| `--tts-voice-seed-cache-dir` | `~/.cache/voicemate` | Where `auto` saves its reference |
| `--tts-reset-seed` | off | Delete the VoxCPM2 auto seed (`voice_seed.wav`) before starting |
| `--tts-show-progress` | off | Show the VoxCPM2 progress bar |
| `--tts-drain-timeout-seconds` | `60.0` | Timeout of the audio player drain |
| `--tts-debug-vram` | off | Log VRAM use around each sentence |

The provider token is never a flag: it is read from `VOICEMATE_TTS_TOKEN`, else from `tts_external_token` in the engine file, and it is never logged. The companion passes it in the environment, and the engine removes it from its environment once read. It stays in plain text in the companion's `companion.toml`.

The companion passes the address and the voice as `--tts-url=...` and `--tts-external-voice=...`, with an `=`, so a value that starts with `-` is not read as a flag.

TTS only starts when a chat flow is on, and so does its warmup: the built-in Kokoro voice is not warmed up otherwise. If the packages of the chosen engine are missing, or the engine fails to start, the engine logs a warning with the fix and runs without TTS.

## Engine file: `~/.config/voicemate/config.toml`

A flat TOML file written by `make setup` and `make configure`. Invalid values are ignored; a missing or broken file means "nothing saved". Every key is optional.

| Key | Values | Meaning |
|---|---|---|
| `gpu_vendor` | `nvidia`, `amd`, `cpu` | GPU vendor |
| `whisper_backend` | `faster-whisper`, `whispercpp`, `openai-whisper` | Speech-to-text engine |
| `tts_enabled` | `true`, `false` | TTS on or off |
| `tts_engine` | the default engine, `kokoro`, `voxcpm`, `external`, `none` | TTS engine |
| `tts_external_url`, `tts_external_voice`, `tts_external_options` | text | Settings of the `external` engine (address, voice id, JSON options) |
| `tts_external_token` | text | Bearer token of the `external` engine. Plain text in this file; `VOICEMATE_TTS_TOKEN` wins |
| `default_flow` | `clipboard`, `claude_chat` | `clipboard` turns the Claude flow off |
| `platform` | `windows`, `linux-x11`, `linux-wayland`, `wsl2` | Runtime environment |
| `trigger` | `keyboard-hooks`, `pynput`, `evdev`, `socket` | Hotkey mechanism |
| `stt_strategy` | `auto`, `faster-whisper-rocm`, `whispercpp`, `openai-whisper` | AMD fallback chain |
| `ct2_rocm_ok` | `true`, `false` | CTranslate2-ROCm validated by setup. The engine writes `false` when it fails at runtime; `make configure` tries again |
| `daemon_port` | integer | HTTP API port |
| `stt_mode` | `auto`, `cpu`, `gpu` | Default transcription mode (asked by `make setup` and `make configure`) |

## Companion file: `companion.toml`

Location: Windows `%APPDATA%\VoiceMate\companion.toml`; Linux `${XDG_CONFIG_HOME:-~/.config}/voicemate/companion.toml`. The uninstaller keeps it. A file that cannot be read is moved aside as `companion.toml.broken-<timestamp>` and the notification "Settings reset" says so. A file written by a newer version is shown read-only.

| Key | Default | Allowed values | In the UI |
|---|---|---|---|
| `version` | `1` | integer | |
| `client_key` | generated on first run | string | |
| `language` | `auto` | `auto`, `pt-BR`, `en`, `es`, `ru`, `zh-CN` | General > "Language:" |
| `dictation_language` | `interface` | `interface` (follow `language`), `auto` (Whisper detects each recording), or one language: `pt`, `en`, `es`, `ru`, `zh`, `fr`, `de`, `it`, `ja` | General > "Dictation language:" ("Same as the interface", "Detect automatically", then one language) |
| `engine_mode` | `wsl2` on Windows, `local` on Linux | Windows: `wsl2`, `external`; Linux: `local`, `external` | General > "Mode:" ("Start it in WSL", "Start it on this computer", "Connect only") |
| `wsl_distro` | `""` (the default WSL distro) | distro name | General > "WSL distro:" |
| `engine_dir` | `""` (detect) | path relative to `$HOME`, or absolute; `"`, `$`, backtick, backslash and newline are rejected | General > "Engine folder:" |
| `daemon_port` | `47821` | 1024 to 65535 | file only |
| `cues_enabled` | `true` | `true`, `false` | Sounds > "Play sound cues"; tray menu > "Mute sounds" |
| `master_volume` | `0.8` | 0.0 to 1.0 | Sounds > "Volume:" |
| `wsl_restart_policy` | `auto` | `auto`, `ask`, `never` | General > "Restart WSL when audio fails:" ("Automatically", "Ask first", "Never") |
| `notify_level` | `warnings` | `all`, `warnings`, `errors`, `none` | General > "Notifications:" ("All", "Warnings and errors", "Errors only", "None") |
| `start_at_login` | `false` | `true`, `false` | General > "Start VoiceMate when I sign in" |
| `tray_icon_visible` | `true` | `true`, `false` (Windows 11 only) | General > "Always show the VoiceMate icon on the taskbar" |
| `stt_mode` | `auto` | `auto` (the engine's own setup), `cpu`, `gpu` | General > Transcription > "Run on:"; tray menu > "Transcription" |
| `stt_cpu_model` | `large-v3-turbo` | `large-v3-turbo`, `large-v3`, `medium`, `small` | General > Transcription > "CPU model:" |
| `stt_gpu_model` | `large-v3-turbo` | same as `stt_cpu_model` | General > Transcription > "GPU model:" |
| `tts_engine` | `default` | `default` (the engine's own setup), `external` | Voice > "Voice engine:" ("Built-in (engine setup)", "Custom provider (HTTP)") |
| `tts_url` | `http://127.0.0.1:8765` | an `http://` or `https://` address without spaces, quotes or `$` | Voice > "Provider address:" |
| `tts_voice` | `""` (the provider's default) | a voice id without spaces or shell characters | Voice > "Voice:" |
| `tts_token` | `""` | text without line breaks. Plain text in this file | Voice > "Token:" |
| `[assistant]` table | see [Assistant settings](#assistant-settings) | | Assistant tab |
| `[[hotkeys]]` with `flow` and `chord` | `clipboard` = `ctrl+alt+v`, `claude_chat` = `ctrl+alt+a` | Ctrl, Alt, Shift or Win with a key, or a function key alone; F12 is reserved by Windows | Hotkeys tab |
| `[cues.<name>]` for `start`, `transcribing`, `ready`, `ai_ready`, `warning`, `error`, `answer` (an answer of another conversation is waiting) | `enabled = true`, `source = "preset"`, `preset = "classic"`, `file = ""`, `volume = 1.0` | `source`: `preset`, `file`; `preset`: `classic`, `soft`, `click`; `file`: a PCM WAV (8 or 16 bit) | Sounds tab |

The language change asks "Restart VoiceMate?" ("Restart now" or "Later"); until the restart, the General tab shows "Takes effect after VoiceMate restarts.".

### Dictation language

The companion turns `dictation_language` into two flags for the engine it starts: `--transcription-language <code>` and `--output-lang <BCP-47>` (`pt` gives `pt-BR`, `zh` gives `zh-CN`, the others their own code). `interface` uses the interface language; `auto` passes `--transcription-language auto` and keeps `--output-lang` on the interface language, so Claude still answers in it. Changing the key restarts the engine. In the mode "Connect only" the key is ignored and the engine keeps its own flags, and so does an engine the companion attaches to (a systemd unit, or one started by hand). More in [usage.md](usage.md#dictation-language).

## Speech-to-text modes

The engine transcribes either on the processor or on the graphics card. The CPU mode exists
so VoiceMate keeps working while the GPU is busy with other work (video editing, rendering, games):
it never loads anything on the GPU.

| Mode | Backend | Decoding | When |
|---|---|---|---|
| `cpu` | faster-whisper, int8 | beam 1, VAD filter, `--cpu-threads` | the GPU is busy, or there is none |
| `gpu` | faster-whisper CUDA (NVIDIA), openai-whisper ROCm (AMD in WSL2), whisper.cpp Vulkan (AMD on Windows) | beam 1 | fastest |
| `auto` | the engine's setup choice | the backend's default (beam 5) | as before this option existed |

The companion keeps one model per mode (`stt_cpu_model`, `stt_gpu_model`) and passes
`--stt-mode` and `--model` to the engine it starts. Switching restarts the engine, which is
what frees (or takes) the GPU. A mode that is not installed is installed by the companion
after asking, with the download size; the same tools run by hand:

```bash
make stt-check
make stt-install MODE=gpu
make stt-install MODE=cpu ARGS="--model medium"
```

`stt-install` only touches the engine's own virtual environment (and `~/.cache` for the
models): ROCm PyTorch and openai-whisper for AMD, CUDA libraries for NVIDIA, the
whisper.cpp build on Windows, the faster-whisper model for the CPU.

### Benchmark

Measured on 2026-10-27 (CPU) and 2026-10-28 (GPU) with Portuguese dictation from Common
Voice (short recordings of about 6 s, long ones of about 65 s), through the engine's own
backends on one machine (Intel i5-13400F, AMD Radeon RX 9070 XT with ROCm in WSL2). WER is
the word error rate against the reference text; latency is the transcription time per
recording.

| Mode | Model | Beam | Short: latency, WER | Long: latency, WER |
|---|---|---|---|---|
| CPU (8 threads, int8) | large-v3-turbo | 1 | 4.7 s, 5.0 % | 21 s, 3.3 % |
| CPU (8 threads, int8) | medium | 1 | 3.2 s, 5.3 % | 15 s, 5.1 % |
| GPU (Radeon, ROCm) | large-v3-turbo | 5 | 0.45 s, 4.8 % | 4.6 s, 2.9 % |
| GPU (Radeon, ROCm) | large-v3-turbo | 1 | 0.18 s, 4.5 % | 1.9 s, 3.6 % |
| GPU (Radeon, ROCm) | medium | 5 | 0.83 s, 6.1 % | 10.1 s, 6.9 % |
| GPU (Radeon, ROCm) | medium | 1 | 0.42 s, 5.8 % | 4.9 s, 7.3 % |

On the CPU, beam 5 gave no lower WER in the same run and was only slower (not in the
table). On the GPU, greedy decoding is about 2.4 times faster than beam 5 for less than
one point of WER, so the `gpu` mode decodes greedily. `medium` is less accurate than
`large-v3-turbo` on both, and on the GPU it is also slower, so the turbo model is the
default of both modes; `medium` and `small` are there for slower processors.

## Assistant settings

The voice assistant ([usage.md](usage.md#voice-assistant)) keeps its choices in the `[assistant]` table of `companion.toml`:

| Key | Default | Allowed values | In the UI |
|---|---|---|---|
| `mode` | `off` | `off`, `own_session`, `desktop` | Assistant > "Assistant mode:" ("Off", "VoiceMate's own conversation", "A Claude Desktop conversation") |
| `desktop_conversation` | `""` | the exact title of a conversation in the Claude Desktop sidebar | Assistant > "Desktop conversation:" (the `desktop` mode does not start without it) |
| `model` | `haiku` | `haiku`, `sonnet`, `opus` (the Claude Code aliases) | Assistant > "Model:" |
| `effort` | `low` | `low`, `medium`, `high` | Assistant > "Effort:" |
| `new_conversation_model` | `sonnet` | same as `model` | Assistant > "New conversations use:" |
| `new_conversation_effort` | `medium` | same as `effort` | Assistant > "With the effort:" |
| `permission_mode` | `acceptEdits` | `default`, `acceptEdits`, `bypassPermissions` | Assistant > "Permissions:" ("Ask before every change", "Accept file edits, ask for the rest", "Full access, never ask (risky)") |
| `answer_delivery` | `announce` | `speak`, `announce`, `sound`, `silent` | Assistant > "Answers from other conversations:" |
| `speak_replies` | `true` | `true`, `false` | Assistant > "Speak the replies" |
| `voice_source` | `auto` | `auto`, `engine`, `windows` | Assistant > "Voice source:" ("Automatic", "The engine's voice", "The voices of Windows") |
| `voice` | `""` (the default voice of the source) | a voice id of the engine's voice engine, or a `windows/...` id of a voice of Windows | Assistant > "Assistant voice:" (**Load voices**, **Test**) |
| `voice_commands` | `true` | `true`, `false` | Assistant > "Understand voice commands in typed text" |
| `expressions` | `off` | `off`, `on` | Assistant > "Expression tags:" |
| `user_name` | `""` | text | Assistant > "Your name:" |
| `personality_source` | `none` | `none`, `file`, `custom` | Assistant > Personality > "Source:" ("None", "A Markdown file (for example a note in your Obsidian vault)", "Write my own") |
| `personality_file` | `""` | empty, or an existing `.md` file (needed when the source is `file`) | Assistant > "Personality file:" |
| `obsidian_vault` | `""` | a folder | Assistant > "Vault folder:" |
| `skill_language` | `auto` | `auto` (the Portuguese skill when the interface is Portuguese, English otherwise), `en`, `pt-BR` | Assistant > "Skill language:" |
| `custom_skill_dir` | `""` | empty (the packaged skill), or a folder that holds a `SKILL.md` | Assistant > "Custom skill:" |
| `desktop_profile_auto_update` | `true` | `true`, `false` | Assistant > "Check for a newer profile when the assistant starts" |
| `desktop_profile_override` | `""` | a profile file imported with **Import...** | Assistant > **Import...**, **Use default** ([desktop-profile.md](desktop-profile.md)) |
| `talk_hotkey` | `ctrl+alt+s` | a chord, or `""` for none | Hotkeys > "Talk to the assistant" |
| `window_hotkey` | `ctrl+alt+w` | same | Hotkeys > "Open the assistant window" |
| `digest_hotkey` | `ctrl+alt+r` | same | Hotkeys > "Hear what's ready" |
| `mode_hotkey` | `""` | same | Hotkeys > "Switch the assistant mode" |
| `back_hotkey` | `""` | same | Hotkeys > "Back to the assistant" |

An invalid value falls back to its default with a log line. An older file with `enabled = true` (or `mode = "on"`) is read as `own_session`, and a `personality_file` without a `personality_source` is read as the source `file`. The assistant hotkeys are checked with the others: a chord cannot be given to two actions.

What a change does while the assistant runs:

| Change | Effect |
|---|---|
| `mode`, `skill_language`, `custom_skill_dir` and, in the own mode, `effort` and `permission_mode` | Restarts the central conversation |
| `desktop_conversation` (Desktop mode) | Restarts the central conversation |
| `model` (own mode) | Applies from the next answer, without a restart |
| `model` or `effort` (Desktop mode) | VoiceMate cannot change the Desktop conversation; it sends the central a note asking it to apply them to itself |
| `permission_mode` | Applies to the conversations the assistant starts from then on, and restarts the central conversation at once (own mode), so it never keeps the old permissions |
| Name, personality, vault, tags, `answer_delivery` | Written to `assistant.json`, which the skill rereads |

The text you write for "Write my own" is not in `companion.toml`: VoiceMate keeps it in `assistant-personality.md` in its state folder (up to 20,000 characters) and gives the skill that file as `personality_file`.

> [!WARNING]
> `permission_mode = "bypassPermissions"` lets every conversation the assistant starts change any file and run any command on this computer without asking. Only the Settings window can choose it: by voice the assistant can switch between `default` and `acceptEdits` only.

The assistant's replies are spoken by the engine, using the voice configured in the engine (Voice tab, or the engine's `--tts-engine`): the companion sends the `<voice>` block of each reply, sentence by sentence, to the engine's `/tts` endpoint ([tts-provider-protocol.md](tts-provider-protocol.md#the-engine-speaks-the-same-protocol)) and plays the audio on this computer.

The built-in Kokoro voice and a custom provider can speak for the assistant, and so can the voices Windows already has (every language installed under Settings, Time and language, Speech), with nothing to download. "Voice source:" decides: `auto` uses the engine's voice and the voices of Windows when the engine cannot speak (or has no voice), `engine` only the engine, `windows` only Windows. **Load voices** and **Test** follow the source picked, before it is saved; a voice id belongs to one source, and each source ignores the other's ids. When nothing can speak, the replies stay text only and the assistant window shows one "No voice available" notice with the reason. "Stop talking", a new prompt and a mode change silence it at once. It never speaks over a recording: what comes while the microphone records, or while the recording is transcribed, waits; a sentence a recording cut is said again from its start, followed by the sentences that were already queued behind it, in order.

VoiceMate also writes `~/.voicemate/assistant.json` in your user folder, which is the file the `voicemate-assistant` skill reads (`version`, `language`, `user_name`, `personality_file`, `notes_vault`, `expressions`, `mode`, `answer_delivery`, `inbox` and the `settings` catalog). With the Portuguese skill, `language` is `"pt-BR"` and the voice speaks pt-BR, whatever the interface language. With the English skill (or a custom one), the language follows the interface. `pending` lists the requests the assistant is waiting on (session id, title, reason, since when). Its format is in [usage.md](usage.md#the-skill). The skill itself is installed in `<Claude home>/skills/voicemate-assistant`, where the Claude home is `~/.claude` or the folder in `CLAUDE_CONFIG_DIR`.

### Settings the assistant can change by voice

The `settings` list in `assistant.json` is the only list the assistant may change.

Assistant keys: `assistant.mode` (`own_session`, `desktop`), `assistant.model`, `assistant.effort`, `assistant.permission_mode`, `assistant.answer_delivery`, `assistant.new_conversation_model`, `assistant.new_conversation_effort`, `assistant.desktop_conversation`.

Voice, text and sound keys: `assistant.speak_replies`, `assistant.voice`, `assistant.voice_commands`, `assistant.expressions`, `assistant.user_name`, `assistant.personality_source`, `assistant.desktop_profile_auto_update`, `assistant.skill_language`, `cues.enabled`, `cues.volume` (0 to 100), `cues.answer.enabled`, `cues.answer.volume` (0 to 100), `general.dictation_language`, `general.stt_mode` and `general.notify_level`.

Left out on purpose: hotkeys; every file or folder (the personality file and text, the vault, the custom skill folder, the imported Desktop profile); the voice engine, the provider address and token; the engine itself (folder, WSL distro, port, mode); the mode `off`; and the sound cues other than the answer one. A value must be one of the listed ones (text is a single line of at most 200 characters), `desktop` needs a chosen Desktop conversation, and `personality_source` `file` needs a chosen file; otherwise VoiceMate refuses with the reason and tells the assistant.

## Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `VOICEMATE_LANG` | engine | Language of the engine messages only (`pt-BR`, `en`, `es`, `ru`, `zh-CN`); without it the messages follow `--output-lang`, whose default is Portuguese. Set it in the shell that runs `make run` (bash inside WSL or on Linux, `$env:VOICEMATE_LANG` in PowerShell on Windows). Wins over `--output-lang`. It does not change what Whisper hears or the language of Claude's answers. The companion ignores it |
| `VOICEMATE_TTS_TOKEN` | engine | Bearer token for the external TTS provider. Wins over the saved one. The companion sets it when it starts the engine |
| `VOICEMATE_API_TOKEN` | engine, companion | Replaces the token file. For tests only |
| `VOICEMATE_CLAUDE_CLI` | companion (assistant) | Path or command name of the Claude Code CLI the assistant starts. Without it, VoiceMate looks for `claude` on the `PATH`, then in `~/.local/bin` (`claude.exe`, the native installer) and `%APPDATA%\npm` (`claude.cmd`, npm). Set it with a full path when `claude` is somewhere else |
| `CLAUDE_CONFIG_DIR` | companion (assistant) | Claude home, where the skill is installed and the conversations are read; default `~/.claude` |
| `PULSE_LATENCY_MSEC` | engine (Linux, WSL2) | Audio buffer of PulseAudio; set to `200` when absent |
| `CT2_CUDA_ALLOCATOR` | engine (CTranslate2-ROCm) | Set to `cub_caching` when absent (workaround for a memory fault on gfx1201) |
| `MIOPEN_FIND_MODE`, `MIOPEN_USER_DB_PATH`, `PYTORCH_TUNABLEOP_*` | engine (AMD only) | Set when absent; caches go to `~/.cache/voicemate` |
| `PULSE_SERVER` | `make doctor` | Checked on WSL2 (expected `unix:/mnt/wslg/PulseServer`) |
| `APPDATA`, `LOCALAPPDATA` (Windows); `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` (Linux) | companion | Base folders of the companion files |
| `LC_ALL`, `LC_MESSAGES`, `LANG`, `LANGUAGE` | companion on Linux | Interface language when "Language:" is automatic. On Windows, the Windows display language decides |

## API token and port

- The HTTP API listens on `127.0.0.1` only, port `47821` by default. WSL2 forwards it to the Windows loopback.
- Requests with an `Origin` header (browsers) or a `Host` that is not loopback get `403`. `/trigger` accepts POST only.
- With `--api-token`, the engine creates `~/.config/voicemate/api-token` (mode 0600, 32 random bytes) when it is missing, and every route except `/health` needs `Authorization: Bearer <token>`. On Windows the companion reads the file through `wsl.exe`, and reads it again after a refusal, at most every 30 seconds.
- `make run` and the systemd service run without a token; `make run-engine` (the companion) runs with it. The old hotkey scripts only work with an engine without a token.

## Files and logs

| File | Windows | Linux |
|---|---|---|
| `companion.toml` | `%APPDATA%\VoiceMate\` | `${XDG_CONFIG_HOME:-~/.config}/voicemate/` |
| `companion.log`, `engine.log` (rotated, 5 MB, 3 backups) | `%LOCALAPPDATA%\VoiceMate\logs\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/logs/` |
| `pending.json` (the **Not copied** list; holds transcription text) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-session.json` and the folder `assistant\` (session id, working folder of the assistant) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `assistant-inbox.json` (answers held for "Hear what's ready"; holds their text), `assistant-conversations\` (the conversations the assistant started), `assistant-personality.md` (the personality you write) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| `desktop-profile\` (the downloaded and imported Claude Desktop profiles) | `%LOCALAPPDATA%\VoiceMate\` | not used |
| `assistant.json` (read by the skill), the skill `voicemate-assistant` | `%USERPROFILE%\.voicemate\`, `%USERPROFILE%\.claude\skills\` | `~/.voicemate/`, `~/.claude/skills/` |
| Rendered sound cues | `%LOCALAPPDATA%\VoiceMate\cues\` | `${XDG_CACHE_HOME:-~/.cache}/voicemate/cues/` |
| Engine choices and API token | inside WSL: `~/.config/voicemate/config.toml` and `api-token` (native Windows engine: `%USERPROFILE%\.config\voicemate\`) | `~/.config/voicemate/` |
| Engine caches (whisper.cpp binaries and models, voice seeds, AMD caches) | inside WSL: `~/.cache/voicemate/` (native Windows engine: `%USERPROFILE%\.cache\voicemate\`) | `~/.cache/voicemate/` |
| systemd service log | `journalctl --user -u voicemate -f`, inside WSL | same command |

Tray menu > **Engine** > **Open logs** opens the logs folder.
