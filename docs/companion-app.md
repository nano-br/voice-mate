# VoiceMate Companion: architecture and protocol contract

Purpose: the design and contract document of the companion app (daemon API v2,
companion core, companion UI, packaging), for contributors. For installing and using
VoiceMate, start with the [README](../README.md); the diagrams are in
[architecture.md](architecture.md). Code contracts: `app/protocol/models.py`
(daemon <-> companion payloads) and `app/companion/contract.py` (companion core <-> UI).
When this document and the code disagree, fix one of them in the same change.

## Why

On WSL2 the engine (Whisper on the AMD GPU) runs as a daemon inside the distro and
the Windows side is a PowerShell script. Problems this design addresses:

- No single place to start, supervise and quit everything. When WSLg's PulseAudio
  dies (it does when Windows loses its microphone), only `wsl --shutdown` brings it
  back, and nothing does that automatically.
- The user cannot see "transcribing" or "ready": Windows only shows the
  microphone-in-use indicator while recording.
- Hotkeys and sound cues are not configurable without editing code.
- Rarely, a transcription is produced but never reaches the Windows clipboard.
  The script now retries and warns, but nothing reconciles after the fact.
- Linux users should get the same optional UI, or keep the plain CLI.

## Decisions

- **Stack:** PySide6 (Qt 6 Widgets) in this repo, packages `app/companion/` and
  `app/protocol/`. Windows-only pieces use ctypes/pywin32. Frozen with PyInstaller
  (onedir) and installed per user with Inno Setup. Runner-up: Tauri 2 (the companion
  only talks HTTP to the engine, so switching later stays possible).
- **Roles:** the engine is always a headless daemon (today's app + HTTP API). The
  companion is the desktop shell: tray, hotkeys (where the OS allows), sound cues,
  clipboard delivery with verification, notifications, supervision, settings.
- **Transport:** HTTP on loopback with a long-poll `GET /events`.
- **i18n:** every user-facing string through `app.i18n._` (msgid English; catalogs
  pt_BR, en, es, ru, zh_CN; the en msgstr stays empty). The `language` setting
  (`UiLanguage`) is `auto`, `pt-BR`, `en`, `es`, `ru` or `zh-CN`; `auto` maps the OS
  UI language by prefix (`pt*` to pt_BR, `es*` to es, `ru*` to ru, any `zh*`, zh_TW
  and zh_HK included, to zh_CN, anything else to English). Our widgets get their text
  from `_()`; only Qt's built-in strings (standard context menus, file dialogs) use
  Qt's own qtbase translations (`qtbase_pt_BR.qm`, `qtbase_es.qm`, `qtbase_ru.qm`,
  `qtbase_zh_CN.qm`, installed with a `QTranslator` after `set_language`). For zh_CN,
  `install_script_fonts` appends Han fonts (Microsoft YaHei UI, then the usual Linux
  CJK families) to the application font: Segoe UI has no Han glyphs, and on a Windows
  that is not in Chinese Qt's own fallback draws them from a Japanese font. Windows grow
  with their texts (the settings dialog has no fixed minimum size; the shortcut fields fit
  their longest prompt), since Russian labels run longer than English. The
  companion picks its language ONCE at startup with `app.i18n.set_language(lang)` (owned by `app/i18n`, called by
  `app/companion/main.py`; ignores `VOICEMATE_LANG`; `setup_locale` keeps its
  behavior for the engine):
  the `language` setting, or for `auto` the OS UI language. It never flips mid
  session, and it never relays the daemon's localized `message` text: it builds
  its own strings from error/warning codes (the daemon may speak another language).
  So a language change asks to restart: when Apply/OK saves a language that selects
  another catalog than the running one (`app.i18n.catalog_for`, shared with
  `set_language`; `auto` and the explicit choice of the same catalog need no
  restart), the app asks "Restart
  VoiceMate?" with "Restart now" / "Later". "Later" keeps the setting saved, and the
  Language row shows "Takes effect after VoiceMate restarts." while the selected
  language differs from the running one. "Restart now" restarts the companion (see
  "Single instance, taskbar, autostart"); the engine it started restarts with it
  (about 10 s without transcription).
- **Settings:** discrete options are `Literal` types (`app/companion/contract.py`).
  Companion settings: `%APPDATA%\VoiceMate\companion.toml` (Linux:
  `~/.config/voicemate/companion.toml`); layout below. Engine settings stay in
  `~/.config/voicemate/config.toml`.
- **Dictation language:** the engine pins one transcription language and, from the
  same code, the language of Claude's answers and of the TTS voice, and its default is
  Portuguese, so the companion always passes both flags. The `dictation_language`
  setting (`DictationLanguage`, Settings > General > "Dictation language") is
  `interface` (default: follow the UI language, `pt-BR` to `pt`, `en`, `es`, `ru`,
  `zh-CN` to `zh`; with the UI language on `auto`, the language of the catalog the OS
  language selects (`app.i18n.catalog_for`), English when it has none), `auto` (Whisper detects each utterance; Claude keeps
  answering in the UI language) or one language: `pt`, `en`, `es`, `ru`, `zh`, `fr`,
  `de`, `it`, `ja` (a subset of the engine's `TranscriptionLanguage`). The supervisor
  resolves it (`app/companion/dictation.py`) and adds `--transcription-language <code>
  --output-lang <BCP-47>` to the `run-engine` ARGS (`pt` gives `pt-BR`, `zh` gives
  `zh-CN`, the others their own code), so a pinned language sets what is transcribed
  AND the language of Claude's spoken answers. Changing it, or the UI language while
  it follows the interface, restarts the engine like a change of mode, distro, folder
  or port. The flags are resolved with `catalog_for`, never from the catalog already
  loaded: the controller builds its backend before `main.py` calls `set_language`, so
  the loaded catalog is still English at that point. In `external` mode the companion
  does not start the engine: the combo is disabled and the engine's own
  `--transcription-language` and `--output-lang` apply. The same holds for an engine the
  companion attaches to without starting it (one already answering `/health` on the
  port, or the systemd unit): it keeps its own flags, so changing the setting does not
  change it. With `auto`, only the transcription is detected, and the engine also gives
  `auto` to the TTS: the default OmniVoice engine then detects the voice language from
  the text, but a saved Kokoro engine has no mapping for `auto` and uses its American
  English voice, so it speaks Portuguese or Spanish answers with that voice. Pin a
  language if you use Kokoro.
- **Python:** the companion supports 3.12 and 3.13 (Windows has 3.13; the engine is
  pinned to 3.12 for the ROCm wheels). It must not import numpy, sounddevice, torch,
  `app.core`, `app.features`, `app.cli`, `app.setup` or `app.daemon`
  (`tests/test_import_boundary.py`).
  Dev environment on Windows: `.venv-companion` from `requirements/companion-dev.txt`.

## Modes (`EngineMode`)

| Mode | Engine | Hotkeys | Clipboard | Cues |
|---|---|---|---|---|
| `wsl2` (Windows default) | daemon in the distro, spawned through `wsl.exe` or attached | companion | companion (Win32, verified, ACK) | companion (`winsound`) |
| `local` (Linux default) | daemon on this machine with its native listener plus the API, spawned or attached | engine (read-only in the UI) | engine | companion (`pw-play`/`paplay`) |
| `external` | never spawned; attach only | as the platform default | as the platform default | companion |
| CLI only (any OS) | unchanged, no companion | engine/script | engine/script | engine |

Capabilities requested at `/register`: `wsl2` = `clipboard` + `cues`; `local` =
`cues`; `external` = as the platform default.

Linux `local` details: the hotkey page shows the engine's chords from `flow_info`
read-only (`check_hotkey` returns `engine_owned`); "copy" from Recent uses
`wl-copy`/`xclip`; without a system tray (`QSystemTrayIcon.isSystemTrayAvailable()`
false, e.g. GNOME without the AppIndicator extension) the status window is the main
UI; start at login via an XDG autostart `.desktop` file; install with the poetry
extra `ui` and `make run-tray`. Windows native (NVIDIA, engine on Windows) is phase 2.

## Daemon API v2

`API_VERSION = 2`. Already in place from the fix branch: `/health` with `pid`,
`instance`, `audio` and `lang`; `instance` in `/status` and `/result`; error events
in the result stream; `POST /shutdown`; browser hardening (requests with an `Origin`
header or a non-loopback `Host` get 403; no `GET /trigger`). Error bodies are
`ErrorResponse` (English, for logs only).

### Startup and readiness

The daemon creates the token file (if `--api-token`), starts the stdin EOF watcher
(if `--supervised`) and binds the HTTP API BEFORE building the transcriber and
handlers (model load takes 10 to 60 s). Until then `/health` answers `ready: false`
and every other endpoint except `/shutdown` and `/unregister` answers 503
`{"error": "not ready"}`, so Quit and Restart work during the load: during the build,
`/shutdown` and stdin EOF exit the process immediately (the model load cannot be
interrupted and there is nothing to clean up yet). When the build
finishes, `ready` becomes true. A daemon that fails to build exits non-zero.

Details settled by the implementation (`app/daemon/`):

- Before `ready`, `flows`, `flow_info` and `tts` in `/health` come from the
  configuration; read them again once `ready` turns true (a flow whose handler
  failed to start, e.g. Claude without `claude login`, disappears then).
- `ready` means "model loaded": the background warmup may still run. A trigger is
  accepted at once; only the first transcription waits for the warmup (the
  backends are not thread-safe).
- Exit codes: 0 after `/shutdown`, stdin EOF or SIGTERM (also during the build, where
  they exit at once, killing a whisper-server the build already started); 1 when the
  build fails or the port is taken (the log then says "Address already in use").
- The Python imports take a few seconds (about 3 s on WSL2) before the API binds;
  until then connections are refused (`starting`, like a refused probe).

### Back-compat

`/status`, `/result`, `/register` with an empty body and `POST /trigger` keep their
v1 behavior for `scripts/windows/voicemate-hotkeys.ps1` and `.ahk`, but only against
a daemon WITHOUT a token (`make run`). The companion replaces the scripts: on first
run, if the default chords are taken (`in_use`), it tells the user to close the
script and remove it from `shell:startup`.

### Auth

Single source: with `--api-token`, the daemon reads `~/.config/voicemate/api-token`,
creating it (mode 0600, 32 random bytes, urlsafe base64) if absent.
`VOICEMATE_API_TOKEN` overrides it, for tests only. With a token, every endpoint
except `/health` requires `Authorization: Bearer <token>` (401 otherwise). The
companion reads the file in spawn AND attach modes, on WSL with
`wsl.exe -d <distro> -e sh -c 'cat "$HOME/.config/voicemate/api-token"'` (`-e` does
not expand `~`) and directly on Linux, as soon as `/health` shows `auth: true`, and reads it again
after a 401, at most every 30 s (a persistent 401 must not run `wsl.exe` on every request);
a token that is still refused gives an "access denied" notification.

### `GET /health`

`HealthPayload`: adds `api_version`, `version`, `uptime_s`, `ready`, `platform`,
`trigger`, `flow_info` (name, kind, engine chord), `tts` and `auth`. A missing
`api_version`, or one below 2, means the engine is older than the companion:
`engine_outdated` in the snapshot, a notification, and in spawn mode an offer to
restart the engine. There is no fallback to v1 polling. A v1 daemon has no `ready`
either: it counts as up for supervision (no start timeout, no restart loop, and its
`audio` never triggers a WSL restart), and triggers are refused with the outdated notice.

### `POST /register`, `POST /unregister`, leases

- `RegisterRequest` (optional; empty = v1) -> `RegisterResponse`, which carries the
  current `snapshot` and the `cursor` it corresponds to: the client renders the
  snapshot, then polls `/events` from that cursor (an operation already in progress
  is visible at once, also after a re-register).
- `clipboard` and `cues` are exclusive leases, one holder each. `lease_s` is clamped
  to 10..120 (default 40). Any request carrying the holder's `client_id` (an
  `/events` poll included) renews its leases.
- `client_key` is stable per install (persisted in companion.toml): registering with
  the same key takes over that key's leases at once (relaunch after a crash).
- `POST /unregister` (`UnregisterRequest`) releases them at once (Quit).
- Every `EventsResponse` lists the `leases` the client holds after that poll. A
  requested lease missing from it expired (the client stalled for more than
  `lease_s`): the client registers again with its `client_key` (takeover).
- Cue responsibility follows the `cues` lease exactly like delivery follows the
  `clipboard` lease: `StateData.needs_cue` / `ResultData.needs_cue` tell whether this
  client must play the cue. The companion plays a cue only when `needs_cue` is true,
  so an expired lease never produces double cues (daemon + companion).
- When a lease expires or is released, the daemon resumes that job itself: it plays
  its own beeps again (`cues`), and writes the clipboard itself where it can
  (`clipboard`; results then get `delivery: "daemon"`).
- After a daemon restart the old `client_id` is unknown: `/events`, `/results/ack`
  and `/unregister` answer 410 and the client registers again. A `reset` caused by an instance change also means
  "register again".
- An empty `/register` body still returns the whole `RegisterResponse` (`granted`
  empty), a superset of v1's `{"client_id"}`.
- A client blocked in an `/events` long poll counts as present: its leases do not
  expire during the poll, whatever `lease_s` and `wait` are.

### `GET /events?client_id=&instance=&since=<cursor>&wait=<0..25>`

`EventsResponse`, events typed as the `Event` tagged union. Returns as soon as there
is an event with `seq > since`, or after `wait` seconds with an empty list. Ring
buffer of 512 events. Client HTTP timeout: `wait` + 5 s.

- Different `instance`, or a cursor older than the buffer: `reset: true`, and the list
  starts with a `snapshot` event (`SnapshotData`).
- `state` (`StateData`) on every state OR phase change. `flow` is the flow that
  started the recording while `recording`, and the destination flow (whose hotkey
  stopped it) while `processing`. `phase`: `transcribing`; then, for a `claude_chat`
  destination, `thinking` and `speaking` (when TTS is on). `mic_live` turns true
  (with its own `state` event) when the capture stream is actually open: on WSLg
  that can take seconds, and the start cue must mean "speak now". It is false
  outside `recording`. Cues fire only on live transitions, never when rendering a
  snapshot (`RegisterResponse.snapshot` or a `reset`).
- `result` (`ResultData`): `needs_delivery` is true for the client that held the
  clipboard lease when it was published. `final` marks the last result of the
  operation (a `claude_chat` operation publishes its transcript first, non-final,
  then the AI response, final). `spoken`: TTS already said it.
- `error` (`ErrorData`): `mic_unavailable | transcription_failed | chat_failed`.
- `warning` (`WarningData`): `time_limit_soon | no_speech | no_audio | slow_backend`.
- `health` (`HealthData`) when the audio state changes.
- `shutdown` (`ShutdownData`) before stopping; it wakes every pending long poll.
  The daemon's own reasons: `supervisor` for stdin EOF and SIGTERM, `user_quit` for
  Ctrl+C in its console and for a `/shutdown` without a reason.

Parameters: `client_id` is required (400 without it); `wait` defaults to 0 and is
capped at 25; a missing `instance` or `since` is answered with a reset. While
`idle`, `StateData.flow`, `flow_kind` and `phase` are null and `op_seq`/`client_id`
are those of the last operation. Results, errors and warnings carry the `op_seq`
they belong to:

- A restart (`restarted`) supersedes the operation in processing: its pending AI
  work is cancelled (Claude is not called, or its answer is discarded), but a
  transcript already being produced is still published, as the operation's
  `final` result when no AI response will follow. Warnings and errors of a
  superseded operation are not published.
- `/cancel` drops the operation: no result, error or warning is published for its
  `op_seq` (its state `idle` event still is).
- An operation may end without a final result (a restart or cancel during
  `thinking`/`speaking`, an empty AI answer, `chat_failed`): state `idle` ends it.
- Error codes also reach the v1 `/result` stream (the scripts show any code).

### Delivery, ACK and reconciliation

- Unacked = `delivery == "pending"`. `POST /results/ack` (`AckRequest` ->
  `AckResponse`; 409 on instance mismatch). `GET /results?state=unacked|recent&limit=10`
  (`ResultsResponse`; `age_s` is computed by the daemon: never compare `created_ts`
  with the client clock, the WSL VM clock drifts after host sleep). `limit` is
  1..100; `unacked` lists the oldest pending results, `recent` the newest results,
  both in ascending `result_seq`. Optional `client_id` (410 when unknown, renews its
  leases) and `instance` (409 on mismatch). The daemon keeps the last 200 results.
- ONE delivery queue keyed by `(instance, result_seq)`: `/events` results and
  reconciliation only enqueue what is not already queued, in flight or ACKed, so a
  result is never set twice.
- The companion delivers `needs_delivery` results in `result_seq` order. A failing
  delivery blocks later ones for at most 3 retries (1 s apart), then it is ACKed
  `failed`, a notification says so, and the text goes to the Not copied list (saved
  to `pending.json`, see below) as well as Recent.
- Clipboard delivery spec (port of the script's `Set-ClipboardReliable`): skip an
  attempt while the clipboard is locked by another process (non-blocking open);
  set, read back, compare (tolerating a trailing newline); up to 5 attempts with
  60..300 ms backoff. Then, for Win+V history, wait 250 ms and, if the clipboard
  still holds our text, set it once more (history coalesces fast changes). All of
  this runs as a timer-driven state machine on the Win32 thread (`SetTimer` /
  `WM_TIMER`), never `sleep`, so hotkeys stay responsive during a delivery.
- Reconciliation every 10 s and on reconnect: `state=unacked`. `age_s` < 30:
  deliver. Older: ONE notification ("a transcription was not copied"), ACK
  `dismissed`, and keep it in the companion's pending list
  (`snapshot.pending_unacked`, `pending_results()`) for a manual copy
  (`copy_result` ACKs it `delivered` when its `instance` is the current daemon
  instance, otherwise it only copies; the last status wins). Never overwrite the
  user's clipboard with stale text automatically. A result delivered by
  reconciliation (its event was missed) plays no cue: its `needs_cue` is unknown.
- An ACK can be dropped (the controller gives up after 5 attempts): a result the
  companion already ACKed that two reconciliations in a row still list as unacked
  gets the same status again, at most once per reconciliation, unless it is queued
  or in flight. One listing alone does nothing (that ACK may still be in transit),
  so a normal ACK in transit is rarely sent twice (and a duplicate is harmless: the
  daemon just sets the same status). Without this, a lost `failed` or
  `dismissed` ACK left the result pending in the daemon, and it came back as stale
  on the next start (also after the user had cleared it).
- Results still queued when the daemon instance changes cannot be ACKed any more:
  they go to the pending list (with the "not copied" notification) instead of being
  written late.
- The pending list (Not copied) survives a Quit or a crash of the companion: once a
  result is in it, the daemon has its ACK and will never offer it again, so the
  companion is the only one left that knows about it. It is saved to `pending.json`
  (see "Local files") on every change and restored at startup, before `start()`, so
  the tray badge and the status window show it at once. A manual copy removes the
  entry (from memory and from the file); "Clear list" (`clear_pending`) empties it,
  see "Tray states, cues and reactions". A restored result the daemon still reports
  as unacked (the old run quit or crashed before its ACK went out) is ACKed
  `dismissed` again and stays in the list; it is never delivered automatically.
- Quit: results still queued or in flight (e.g. a delivery retrying while another app
  holds the clipboard) will not be delivered by this run, and the engine may stop
  with the app and take them along. They move to the pending list before the quit
  sequence flushes `pending.json` (no notification while quitting), without an ACK:
  if the same daemon still reports them on the next start, they are ACKed `dismissed`
  then (as above) and stay in the list. A write that was in flight may still have
  reached the clipboard: listing it anyway is the safe side. Manual copies waiting in
  the queue are dropped (their result is already in the list, or was delivered).

### Other endpoints and flags

- `POST /trigger` (`TriggerRequest` -> `TriggerResponse`). `expect`: `toggle`
  (default, v1 behavior); `start` while recording -> `noop`, while processing ->
  `restarted`; `stop` while idle or processing -> `noop`.
- `POST /cancel` (`CancelRequest` -> `CancelResponse`): no result is published for a
  cancelled `op_seq`.
- `POST /shutdown` (`ShutdownRequest`, body optional; an unknown `reason` is a 400).
  Another client's `user_quit` shutdown puts the supervisor in `stopped`, not a crash
  restart.
- In `TriggerRequest`, an empty `client_id` is the same as none (the daemon assigns
  one); a non-string `flow` or `client_id` is a 400.
- `--supervised`: shut down cleanly when stdin reaches EOF (verified: EOF propagates
  through `wsl.exe`). Killing `wsl.exe` kills the Linux process without cleanup, so
  every stop path tries `/shutdown` and EOF first.
- `--api`: run the HTTP API next to the native hotkey listener (Linux native).
- `--api-token`: see Auth.
- `make run-engine` = `voice-mate --supervised --api --api-token $(ARGS)` with the
  platform's default trigger (socket on WSL2, where `--api` is a no-op). The port
  goes through `ARGS="--daemon-port <n>"`; the companion adds
  `--transcription-language <code> --output-lang <BCP-47>` (see "Dictation language").

## Companion

### Tray states, cues and reactions

| `TrayState` | Badge | Tooltip | Cue on entering |
|---|---|---|---|
| `stopped` | grey octagon, dimmed glyph | "VoiceMate is stopped" | none |
| `starting` / `restarting` | grey ring / circular arrow | "Starting engine..." / "Restarting (attempt 2)" | none |
| `idle` | none | "Listening for Ctrl+Alt+V" | none |
| `recording` | red disc | "Recording 00:12" | none (`start` plays on `mic_live`) |
| `transcribing` | amber hourglass | "Transcribing..." | `transcribing` |
| `thinking` / `speaking` | violet speech bubble / loudspeaker | "Claude is answering..." | none |
| `ready` (lasts 3 s) | green disc with a check | "Copied: ..." | `ready` or `ai_ready` (see below) |
| `warning` | yellow triangle with "!" | "No microphone" / "WSL audio is down" | `warning` |
| `error` | red square with an X | details | `error` |

The glyph is the VoiceMate mark (a figure with raised arms whose body is a sound wave,
and a coral head; see [brand.md](brand.md)) with no tile, in the taskbar's foreground
color, the same size and place in every state; only a small badge in the bottom-right
corner changes. Badges differ by shape, not only color.
The glyph follows the taskbar theme
(`HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\SystemUsesLightTheme`).
No animation by default.

Windows 11 puts every NEW notification-area icon in the hidden overflow (the arrow
next to the clock), where nobody would see these states. With `tray_icon_visible`
(default true; Settings > General > "Always show the VoiceMate icon on the taskbar",
shown on Windows only) the core promotes our icon
(`app/companion/win/tray_visibility.py`, through `Desktop.set_tray_icon_promoted`):
Windows 11 keeps one subkey per icon under `HKCU\Control Panel\NotifyIconSettings`
(REG_SZ `ExecutablePath`, REG_DWORD `IsPromoted`: 1 = on the taskbar, 0 or absent =
in the overflow) and applies `IsPromoted` changes live. Only the subkeys whose
`ExecutablePath` is this process's executable are written (`sys.executable` and the
process image, compared after `normcase(abspath())`; a known-folder prefix such as
`{6D809377-...}\` for Program Files is resolved first). Explorer creates the subkey
asynchronously after the icon first shows, so the core tries 2 s, 10 s and 30 s
after start and stops at the first try that finds an entry. Explorer creates the
entry WITHOUT `IsPromoted` (absent = nobody decided yet), so at startup the core
writes 1 only where the value is absent (`only_if_unset`): an icon the user hid
in the Windows taskbar settings (`IsPromoted` = 0) stays hidden. A change of our
setting is the user's explicit choice and forces the value (true writes 1, false
writes 0 at once, whatever is there). Turning the setting off hides the icon for
good: with it off, startup writes nothing. The settings window only sends what
differs from the saved settings and the core only acts on a change, so bringing
back an icon hidden in Windows takes two applies: turn the option off, click
"Apply", turn it on again and click "OK" (the checkbox tooltip says so). Windows 10 has no such key: nothing
happens there. Errors are logged, never raised.

An event's cue REPLACES the cue on entering the tray state (never two cues). A
`warning` tray state clears on the next `mic_live`, or when `audio` is back to `ok`
and Windows has a microphone.

Cues for daemon events (`state`, `result`, `error`, `warning`) play only when the
event's `needs_cue` is true. Companion-local cues (delivery failed, and tray states
driven by the supervisor or by `health`: audio down, degraded, failed) always play,
since the daemon never beeps for them.

| Event | Tray | Cue | Notification |
|---|---|---|---|
| `state` with `mic_live` turning true | `recording` | `start` | none |
| final result with `needs_delivery` false (engine or daemon wrote the clipboard) | `ready` | as the two rows below | none |
| verified delivery of a `final` transcript | `ready` | `ready` | none |
| verified delivery of a `final` ai_response | `ready` | `ai_ready` unless `spoken` | none |
| non-final result delivered | unchanged | none | none |
| `warning time_limit_soon` | stays `recording` | `warning` | none |
| `warning no_speech` / `no_audio` | `idle` | none | info |
| `warning slow_backend` | unchanged | none | warning (once per session) |
| `error mic_unavailable` | `warning` | `error` | warning (no mic on the OS vs audio stuck) |
| `error transcription_failed` / `chat_failed` | `idle` | `error` | error |
| delivery failed after retries | `idle` | `error` | error |

| `SupervisorState` | `TrayState` |
|---|---|
| `stopped` | `stopped` |
| `starting` | `starting` |
| `healthy` | from the engine state/phase (or `warning` while audio is down / no mic) |
| `degraded` (missed probes, not yet a restart) | `warning` |
| `restarting` / `backoff` | `restarting` |
| `failed` | `error` |

Menu: status line; "Dictate (Ctrl+Alt+V)" / "Ask Claude (Ctrl+Alt+A)", which become
"Stop and copy" / "Stop and ask Claude" while recording; Cancel; Recent (last 10,
click copies) and Not copied (never delivered, click copies); Mute sounds; Engine >
Restart engine / Restart WSL... / Open logs; a "Restart WSL now..." item while
`pending_wsl_restart`; Settings...; Quit VoiceMate. Left click opens the status
window, which shows the same information and a "Quit VoiceMate" button.

Emptying Not copied: the submenu ends with a separator and "Clear list"; the status
window's Not copied group has "Clear" next to "Copy". Both ask first, with a
non-modal question ('Clear the "Not copied" list?', buttons "Clear list" and
"Cancel", Cancel being the default for Enter and Esc): the texts cannot be copied
afterwards, and since the list now survives restarts it holds the only copy of them.
The question remembers the items listed when it opened, and answering "Clear list"
calls `clear_pending(keys)` with exactly those: a delivery that fails while the
question is open is not wiped unseen. They are dropped from memory and the file is
rewritten (deleted when nothing is left). Nothing is ACKed again (those results were
ACKed when they became pending) and cleared results stay known, so reconciliation
never offers them again. The exception is a restored result whose ACK the daemon
never got: it is ACKed `dismissed` at once when it belongs to the current daemon
instance, otherwise when the daemon reports it. Quit closes the question unanswered.
There is no per-item removal: an item leaves the list when it is copied or when the
list is cleared.

### Notifications

Tray balloons (`QSystemTrayIcon.showMessage`) have no buttons and only the last one
is clickable (`NotificationAction`). Anything that needs a decision or a copy lives
in the menu and the status window (`pending_unacked`, `pending_wsl_restart`).
Rate limit: once per code per 5 minutes, except "not copied" (delivery failed or
stale result), which is never dropped: within 5 minutes it is aggregated ("2
transcriptions were not copied"). `notify_level` filters them, except one-time
tips (info level), which only `none` hides: on the first run (no `companion.toml`
yet) on Windows, "Pin VoiceMate to the taskbar" explains how to pin the app (code
`pin_taskbar`, raised once by the core at start).

### Supervisor (WSL2)

- Spawn: `wsl.exe -d <distro> -e bash -lc 'cd "$HOME/<engine_dir>" && exec make run-engine ARGS="--daemon-port <port> --transcription-language <code> --output-lang <BCP-47>"'`
  (the language flags come from `dictation_language`, see Decisions; absolute `engine_dir` without `$HOME`). Never `shlex.quote` a `~` path; reject
  `"`, `$`, backtick, backslash (a trailing one escapes the closing quote) and newline
  in `engine_dir` when settings are applied. Flags:
  `CREATE_NO_WINDOW`; stdin pipe held open; stdout+stderr through a pipe read by a
  thread into a `RotatingFileHandler` (the thread swallows logging errors and never
  stops draining, or the engine would block on write):
  `%LOCALAPPDATA%\VoiceMate\logs\engine.log`
  (companion's own log: `companion.log` there; Linux:
  `${XDG_STATE_HOME:-~/.local/state}/voicemate/logs/`). `wsl.exe` (a stub that
  spawns the real one) goes into a Job Object with KILL_ON_JOB_CLOSE as a safety net.
- Before spawning: if `/health` answers, attach. Then check
  `systemctl --user is-enabled voicemate` through `wsl.exe` (before every spawn, not
  only the first: the unit may be enabled later): if enabled, use attach mode (two
  daemons would race for the port and the VRAM) and notify how to disable it
  (`systemctl --user disable --now voicemate`); the controller has no API for a
  yes/no offer. A spawned engine that exits with "address in use" while `/health`
  answers means attach, not backoff, once: a second conflict before the engine is
  healthy goes through the backoff and the breaker.
- Probing: a 500 ms TCP connect probe (a refused loopback connect takes ~2 s on
  Windows), then `/health` with a 2 s timeout, every 5 s. HTTP through
  `urllib` with `ProxyHandler({})` (no system proxy for loopback).
- Startup grace 240 s: refused connections and `ready: false` both mean `starting`;
  still not ready when the grace ends -> restart the engine (counts toward the breaker).
  A hotkey pressed while starting gives an "engine is starting" notification and
  sends no trigger.
- Engine exited, or 3 missed probes after ready: restart the engine with backoff 2,
  5, 15, 30, 60, 120 s; reset after 10 min healthy.
- WSL restart (`POST /shutdown` first, then `wsl --shutdown` + respawn) when `audio == "down"` twice in a row, or
  on `mic_unavailable` while Windows has an active capture device AND audio is not
  `ok`. With audio `ok`, a mic error points to Windows (privacy settings for desktop
  apps, or another app holding the mic exclusively): notify, never restart.
- `WslRestartPolicy`: `never`; `ask` (always ask: `pending_wsl_restart` + menu item,
  answered with `answer_wsl_restart`); `auto` (restart, but ask when other distros
  are running: `wsl.exe -l -q --running` with `WSL_UTF8=1`, since `--shutdown` stops
  them all, Docker included). Wait until the engine is idle (at most 30 s).
- Our own `wsl.exe` exiting because of `--shutdown` does not count as an engine crash.
- Attach mode (e.g. a systemd unit runs the daemon): after a WSL restart nothing else
  boots the distro, and WSL stops an idle distro even with services running, so the
  companion holds a keepalive `wsl.exe -d <distro> -e sleep infinity` (stdin pipe,
  same Job Object) while attached.
- No capture device on Windows: never restart; warn, poll every 3 s; when a mic
  appears and audio is not `ok`, restart WSL.
- Circuit breaker: 5 engine restarts in 15 min, 3 WSL restarts in 1 h, or 3 start
  timeouts in a row (they are >= 240 s apart, so the 15 min window never sees them) ->
  `failed` plus a notification (the restart that would exceed the limit is not
  attempted); a WSL restart still waiting for an idle engine is dropped. A manual
  "Restart engine" / "Restart WSL..." resets it. In `external` mode "Restart engine" never
  asks the daemon to shut down (nothing could start it again).
  `snapshot.restarts` counts the restarts in the windows, the one in progress included.
- Restart engine and Quit: `POST /shutdown`, wait up to 8 s, close stdin, wait 2 s,
  terminate the job. An attached daemon (not started by this app) gets only
  `/unregister` on Quit and keeps running while something else keeps the distro up
  (Quit also drops the attach-mode keepalive).

### Hotkeys (Windows)

One dedicated Win32 thread with a message-only window owns `RegisterHotKey`
(always with `MOD_NOREPEAT`) and all clipboard writes/read-backs; other threads post
messages to it. A chord needs a modifier, or is F1..F24; F12 is reserved by Windows
(even with modifiers). `check_hotkey` tries a temporary registration (our own
current registration is not `in_use`). The settings window suspends the global
hotkeys while capturing; apply re-registers all of them at once and rolls back on
failure. A chord another app holds at startup (e.g. the old hotkeys script) is
notified once and retried every 10 s until it is ours, the settings change, or Quit.
Stored as `ctrl+alt+v` (the format, key names and display names are defined
in `app/companion/chords.py`). Only flows present in `flow_info` get registered;
until the first `/health` with `flow_info`, every configured chord is registered,
so a press while the engine starts still gets the "engine is starting" notification.

### Sound cues

- Presets are rendered to WAV files with the stdlib (`wave` + `array`), with the gain
  (master volume x cue volume) baked in, under `%LOCALAPPDATA%\VoiceMate\cues\`
  (Linux: `~/.cache/voicemate/cues/`), and re-rendered when settings change, into a
  new file name (content hash): a sound still playing keeps its file open.
- `classic` reproduces the engine's tones (`app/core/audio_feedback.py`): start 660 Hz
  120 ms; transcribing (new) 523 Hz 70 ms; ready 880 Hz 100 ms + 1100 Hz 150 ms;
  ai_ready 523/659/784 Hz triad; warning 440 Hz 200 ms; error 300 Hz 300 ms. `soft`:
  same pitches, lower and longer fades. `click`: short percussive clicks.
- Custom files: PCM 8/16-bit WAV only, validated on apply, rendered with the gain.
- Windows: `winsound.PlaySound(path, SND_FILENAME | SND_ASYNC | SND_NODEFAULT)` (one
  sound at a time per process; `SND_MEMORY` cannot be async). Linux: `pw-play` or
  `paplay`.

### Settings file

Stdlib only (`tomllib` to read, a small writer of our own: `app.setup.persisted_config`
cannot be reused, it imports the engine and only writes flat scalars). Invalid or
unknown values fall back to defaults with a log line; a newer `version` opens the
settings read-only with a warning. A file that is not readable as TOML (or UTF-8) is
never overwritten: it is moved aside to `companion.toml.broken-<YYYYmmdd-HHMMSS>` (a
second broken file never replaces an earlier backup) before the defaults are
written, and a "settings reset" notification says so. `engine_mode` must exist on the
platform (`wsl2` on Windows, `local` elsewhere, `external` everywhere): another value
falls back to the platform default when read and is rejected when applied.

```toml
version = 1
client_key = "6f1c..."
language = "auto"
dictation_language = "interface"
engine_mode = "wsl2"
wsl_distro = "ai-lab"
engine_dir = "ai-lab/voice-mate"
daemon_port = 47821
cues_enabled = true
master_volume = 0.8
wsl_restart_policy = "auto"
notify_level = "warnings"
start_at_login = false
tray_icon_visible = true

[[hotkeys]]
flow = "clipboard"
chord = "ctrl+alt+v"

[[hotkeys]]
flow = "claude_chat"
chord = "ctrl+alt+a"

[cues.start]
enabled = true
source = "preset"
preset = "classic"
file = ""
volume = 1.0
```

`dictation_language` is `interface`, `auto`, `pt`, `en`, `es`, `ru`, `zh`, `fr`, `de`, `it`
or `ja` (see "Dictation language"); an older file without it follows the interface.

`engine_dir` empty means "detect or ask" on first run (look for `Makefile` +
`app/main.py` under common paths inside the distro).

### Local files

`app/companion/paths.py` decides where everything goes:

| File | Windows | Linux |
|---|---|---|
| Settings (`companion.toml`) | `%APPDATA%\VoiceMate\` | `${XDG_CONFIG_HOME:-~/.config}/voicemate/` |
| Logs (`companion.log`, `engine.log`) | `%LOCALAPPDATA%\VoiceMate\logs\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/logs/` |
| Not copied list (`pending.json`) | `%LOCALAPPDATA%\VoiceMate\` | `${XDG_STATE_HOME:-~/.local/state}/voicemate/` |
| Rendered cues | `%LOCALAPPDATA%\VoiceMate\cues\` | `${XDG_CACHE_HOME:-~/.cache}/voicemate/cues/` |

The environment variable always wins, and the home directory is looked up only when
it is missing: a process started without `USERPROFILE`/`HOME` (PowerShell
`Start-Process -UseNewEnvironment`) still finds `%APPDATA%` and `%LOCALAPPDATA%`.
When neither the variable nor a home directory is available, `paths` raises
`DataDirError` (a `RuntimeError` with `variable` and `home_variable`): it never
guesses a folder (settings written to a temporary folder would be lost silently).
`main.py` catches it, logs it, shows a critical message box in the OS language
("VoiceMate cannot find its data folder: APPDATA is not set...", built from a msgid
with the variable names, since the exception text is English; Qt's translations are
installed first, so its OK button speaks the same language) and exits with code 1;
`setup_file_logging` skips companion.log in that case instead of raising. Helpers that only look for optional things under the home directory (the
Linux engine folder detection, the local API token) treat a missing home directory
as "not found".

`pending.json` (`app/companion/pending_store.py`), UTF-8 with LF, items oldest first:

```json
{
  "version": 1,
  "items": [
    {
      "instance": "6c0f...",
      "result_seq": 42,
      "op_seq": 41,
      "kind": "transcript",
      "flow": "clipboard",
      "text": "the transcription",
      "final": true,
      "created_ts": 1727800000.0,
      "delivery": "failed"
    }
  ]
}
```

- `instance` + `result_seq` are the identity `copy_result` and the delivery queue use
  (after a daemon restart the old instance only means "copy, do not ACK"); `age_s` is
  not stored (the daemon computes it per response).
- At most `PENDING_LIMIT` (50) items, the same cap as the list in memory, so a
  restart never drops what the user could see; the oldest go first.
- Written whole on every change (a result added, copied or capped, the list
  cleared), on the `pending` worker thread (temp file + `fsync` +
  `os.replace`; on Windows a `PermissionError` from `os.replace`, an antivirus or the
  search indexer briefly holding the file, is retried up to 5 times, 50 ms more each
  time); Quit waits up to 2 s for the last write. An empty list deletes the file and
  any `pending.json.tmp`. A write that fails is logged (once, until a write works
  again) and the list stays in memory: disk errors never block the UI or a delivery.
  A lone surrogate in a text (JSON can carry one) is written as a `\u` escape, so the
  file stays valid UTF-8.
- Loading never raises (it runs in the controller's constructor, at every start). A
  leftover `pending.json.tmp` (a write cut short by a crash; `pending.json` itself is
  always whole) is deleted first. A file that is missing starts an empty list. One
  that is not readable (not UTF-8, not JSON, an integer over Python's digit limit,
  nesting too deep, another `version`, `items` not a list) is moved aside to
  `pending.json.broken-<YYYYmmdd-HHMMSS>`, never overwritten, logged once, and a
  warning notification ("pending_reset": '"Not copied" list reset', with the backup
  path) tells the user, like "settings reset" does; if it cannot even be moved,
  nothing is written to it for the rest of the session. An item with a missing field
  or a wrong type (a list where a string belongs, a timestamp too large for a float)
  is skipped with a log line, the valid ones are restored.
- Privacy: the file holds transcription text. It stays in the user's profile, only
  holds what is waiting for a manual copy, goes away when the list is empty (also
  after "Clear list", with its `.tmp`), and the Windows uninstaller deletes it (with
  its `.tmp` and `.broken-*` copies). The text is never written to the logs.

### Single instance, taskbar, autostart

- Lock: named mutex `SINGLE_INSTANCE_MUTEX` (`CreateMutexW` + `ERROR_ALREADY_EXISTS`).
  Verified: `QLocalServer.listen()` succeeds twice on Windows, so it is not a lock.
  The QLocalServer (`LOCAL_SERVER_PREFIX` + user name) only forwards
  `--command show|quit|restart-engine|restart-wsl|settings` (`CompanionCommand`).
  Linux: `QLockFile` in `$XDG_RUNTIME_DIR`, plus `QLocalServer.removeServer()` for a
  stale socket.
- `main.py` sets `APP_USER_MODEL_ID` with `SetCurrentProcessExplicitAppUserModelID`
  before creating `QApplication`; the Inno Setup `[Icons]` entry uses the same
  `AppUserModelID`. Set both or neither. Jump list tasks (pywin32
  `ICustomDestinationList`): Settings, Restart engine, Restart WSL... (`wsl2` mode
  only), Quit VoiceMate; each runs `VoiceMate.exe --command <x>`.
- Restart (a language change, `app/companion/relaunch.py`): the running instance first
  starts a detached copy of itself (frozen: `VoiceMate.exe` with its original
  arguments minus `--command`/`--autostart`; from source: `python -m
  app.companion.main`) with the hidden flag `--after-restart`; on Windows with
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB` (without
  breakaway when the job forbids it), on Linux in a new session. Only then does it
  quit like "Quit VoiceMate" (the engine it started stops). The new process never
  forwards "show" to the quitting instance: it polls the lock until that instance
  exits (`RESTART_WAIT_S`, 40 s, above the 20 s quit safety net) and then starts
  normally; past the wait it behaves like any second launch. If the copy cannot be
  started, an error notification says so and the running instance keeps running.
- Clicking the pinned shortcut while running opens the status window. Windows 11
  forbids pinning from an installer: the first run (no settings file yet) shows the
  info notification "Pin VoiceMate to the taskbar" ("Open Start, search for
  VoiceMate, right-click it and choose Pin to taskbar."). Pinning the app is separate
  from the tray icon, which shows on the taskbar by default (`tray_icon_visible`,
  see "Tray states, cues and reactions").
- Start at login: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` with
  `--autostart` (Windows), XDG autostart (Linux). The Run value name is
  `AUTOSTART_RUN_VALUE`, shared by the installer's autostart task and
  `app/companion/win/autostart.py`; its data is `"<exe>" --autostart` (from source, a
  `pythonw -c` bootstrap that puts the checkout on `sys.path`). The installer offers
  that task on a fresh install only; from then on the app's setting owns the value,
  and the OS state is the source of truth for `start_at_login`: the core reads it on
  load and writes it on apply (the uninstaller removes it).

## Packaging

- PyInstaller onedir, windowed; compile the catalogs (`pybabel compile`) and ship
  `app/i18n/locales/*/LC_MESSAGES/voicemate.mo`; exclude everything the engine uses.
- Inno Setup per user (`PrivilegesRequired=lowest`, `{localappdata}\Programs\VoiceMate`),
  `AppMutex=` `INSTALLER_APP_MUTEX`, Start menu shortcut with the AUMID, optional
  autostart, uninstaller runs `--command quit` first, then deletes the logs, the
  rendered cues and `pending.json*` from `%LOCALAPPDATA%\VoiceMate` (the settings in
  `%APPDATA%\VoiceMate` are kept). ISCC is not installed by
  default: `make companion-installer` explains how to get it
  (`winget install JRSoftware.InnoSetup`). The wizard speaks English, Brazilian
  Portuguese, Spanish, Russian (Inno Setup's own translations) and Simplified Chinese:
  Inno Setup ships no Chinese translation, so the unofficial one from the JRSoftware
  translations page is vendored in `packaging/windows/languages/` (see its README).
  Every `[Messages]`/`[CustomMessages]` entry of the script exists in all five
  (`tests/test_companion_packaging.py` checks it).
- Reproducible builds: exact pins in `requirements/companion-constraints.txt`.
- Icons: `app/companion/assets/voicemate.ico` (16 to 256 px) and
  `voicemate-<size>.png`, drawn by `tools/gen_icon.py` (`python -m tools.gen_icon`) with
  the same code as the tray glyphs (`app/companion/ui/icons.py`, see [brand.md](brand.md)).
  The UI loads them from `Path(app.companion.__file__).parent / "assets"`; the frozen
  app ships them at the same place.
- Limits `packaging/windows/voicemate-companion.spec` puts on the UI (the frozen app
  fails or degrades if code goes past them; relax the spec in the same change):
  - Qt modules: only QtCore, QtGui, QtWidgets and QtNetwork. The other PySide6 bindings
    are excluded.
  - No SVG: QtSvg, the `iconengines` plugins and `imageformats/qsvg` are dropped.
  - Images: PNG (built into QtGui) and ICO (the `qico` plugin) only. Platform plugin:
    `qwindows` only.
  - Qt translations: only `qtbase_pt_BR.qm`, `qtbase_es.qm`, `qtbase_ru.qm` and
    `qtbase_zh_CN.qm` are kept, in Qt's
    translations path (`QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)`),
    so `main.py` can install a `QTranslator` for Qt's own texts (the Undo/Cut/Copy/Paste
    context menu of text fields). Everything else comes from gettext.
  - The engine stack (`app.main`, `app.daemon`, `app.core`, `app.features`, `app.cli`,
    `app.setup`, numpy, torch and friends) is excluded: importing it fails at startup.
  - Dynamic imports inside `app.companion` are fine: the spec collects every
    submodule except `linux` packages.

## Repo layout

```
app/protocol/          models.py (API_VERSION, Literals, TypedDicts)        # stdlib only
app/daemon/            events, leases, delivery, auth, readiness (engine side of API v2)
app/companion/         contract.py, controller.py, client.py, model.py, settings_store.py,
                       cues.py, delivery.py (queue + reconciliation), relaunch.py
                       (restart), supervisor/,
                       win/ (hotkeys, clipboard, audio devices, job object, jump list,
                       autostart, single instance), linux/, ui/, main.py
app/companion/assets/  voicemate.ico, voicemate-<size>.png (generated by tools/gen_icon.py)
packaging/windows/     voicemate-companion.spec, voicemate_launcher.py, voicemate-companion.iss
packaging/linux/       voicemate-companion.desktop (launcher template)
tools/                 gen_icon.py (icons), build_installer.py (finds ISCC, `make companion-installer`)
requirements/          companion.txt, companion-dev.txt, companion-constraints.txt
tests/companion/, tests/daemon/, tests/test_import_boundary.py, tests/test_companion_packaging.py
```

Conventions (enforced by `tests/test_import_boundary.py`): Windows-only modules live
under a `win` package and Linux-only modules under a `linux` package (any depth, e.g.
`app/companion/win/hotkeys.py`); anything importing PySide6 (`QLockFile` and
`QLocalServer` included) lives under `ui/` or in `main.py`; every other module must
import on both systems without pywin32 or PySide6.
Companion dependencies have one source, `requirements/companion.txt`; the poetry
extra `ui` lists the same packages and a test checks they match.

Makefile: `companion-venv`, `companion-test`, `companion-lint`, `run-tray`,
`run-engine`, `companion-build`, `companion-installer`.
