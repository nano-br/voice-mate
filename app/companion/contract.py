"""Boundary between the companion core (no Qt) and its UI (PySide6).

The core implements `CompanionController`: it supervises the engine, talks to
the daemon, owns hotkeys, clipboard delivery, sound cues and settings. The UI
(tray, status window, settings window) only renders `CompanionSnapshot`s and
calls controller commands, so each side can be built and tested on its own.

Threading:
- The controller publishes snapshots and notifications from ONE dispatcher thread,
  in order, never holding its own locks (listeners may call commands). `rev`
  increases with every snapshot. `subscribe` delivers the current snapshot at once.
- The UI must hop to the Qt thread itself (e.g. emit a Qt signal from the listener).
- Commands may be called from any thread and return quickly: slow work (HTTP,
  restarting WSL, quitting) runs on the controller's threads.
- On Windows, global hotkeys and clipboard writes live on one dedicated Win32
  thread with a message-only window (RegisterHotKey and OpenClipboard are
  thread/window bound); commands reach it by posting messages.

User-facing strings produced by the core (notification titles/messages, status
text, validation errors) are already localized with `app.i18n._`; the companion
builds them from error/warning CODES, never by relaying the daemon's `message`
(the daemon may speak another language).

Factory: `app/companion/main.py` builds the controller with
`app.companion.controller.create_controller(settings_path: Path | None = None)
-> CompanionController` (imported lazily; `--demo` uses the UI's fake controller).
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Literal, Protocol, get_args

from app.protocol.models import (
    DEFAULT_STT_MODEL,
    AudioHealth,
    FlowKind,
    ResultRecord,
    SttCheckReport,
    SttDevice,
    SttInfo,
    SttMode,
    SttModel,
)
from app.protocol.tts_provider import DEFAULT_URL as DEFAULT_TTS_URL
from app.protocol.tts_provider import Voice

if TYPE_CHECKING:
    from app.companion.assistant.contract import AssistantService

# Shared identity: the Inno Setup shortcut, the jump list and the process must all
# use the same AUMID, or Windows shows a second taskbar button next to the pin.
APP_USER_MODEL_ID: Final = "VoiceMate.Companion"
# The real single-instance lock (CreateMutexW + ERROR_ALREADY_EXISTS); Inno Setup's
# AppMutex uses the name without the "Local\" prefix. QLocalServer is only the
# channel that forwards --command to the running instance.
SINGLE_INSTANCE_MUTEX: Final = "Local\\VoiceMate.Companion"
INSTALLER_APP_MUTEX: Final = "VoiceMate.Companion"
# Value name under HKCU\Software\Microsoft\Windows\CurrentVersion\Run for "start at
# sign-in". The installer's autostart task (packaging/windows/voicemate-companion.iss)
# and app/companion/win/autostart.py must both use it, so they toggle the same entry.
AUTOSTART_RUN_VALUE: Final = "VoiceMate"
LOCAL_SERVER_PREFIX: Final = "voicemate-companion-"  # + user name (pipes are machine-global)

# What the tray shows. `ready` lasts 3 s after a final result reached the clipboard
# (a verified delivery by the companion, or written by the engine/daemon).
TrayState = Literal[
    "stopped",
    "starting",
    "restarting",
    "idle",
    "recording",
    "transcribing",
    "thinking",
    "speaking",
    "ready",
    "warning",
    "error",
]
SupervisorState = Literal["stopped", "starting", "healthy", "degraded", "restarting", "backoff", "failed"]

# wsl2: spawn/attach the engine inside a WSL distro (Windows).
# local: spawn/attach the engine on this machine (Linux native).
# external: never spawn; only attach to whatever answers on the port.
EngineMode = Literal["wsl2", "local", "external"]
WslRestartPolicy = Literal["auto", "ask", "never"]
UiLanguage = Literal["auto", "pt-BR", "en", "es", "ru", "zh-CN"]
# What the engine transcribes: "interface" follows the companion's UI language, "auto" lets
# Whisper detect the language of each utterance, the rest pin one language (a subset of the
# engine's `TranscriptionLanguage`). Resolved to engine flags by `app.companion.dictation`.
DictationLanguage = Literal["interface", "auto", "pt", "en", "es", "ru", "zh", "fr", "de", "it", "ja"]
NotifyLevel = Literal["all", "warnings", "errors", "none"]
# Where the engine this app starts gets its spoken answers: "default" leaves the engine's own
# choice (its saved setup); "external" is a provider that follows docs/tts-provider-protocol.md.
TtsChoice = Literal["default", "external"]

# The voice assistant (docs/research/voice-assistant-design.md): "own_session" is a Claude Code
# conversation VoiceMate starts and owns (the Desktop and the phone are remote clients of it);
# "desktop" is a conversation of the Claude Desktop app, written by a short "flash" of typing
# into its window and read from its transcript.
AssistantMode = Literal["off", "own_session", "desktop"]
AssistantModel = Literal["haiku", "sonnet", "opus"]
AssistantEffort = Literal["low", "medium", "high"]
ExpressionsMode = Literal["off", "on"]
# The permission mode of every conversation VoiceMate starts (the central one and the ones the
# assistant opens). "bypassPermissions" gives those conversations full access to the machine.
AssistantPermission = Literal["default", "acceptEdits", "bypassPermissions"]
# Which packaged skill is installed: "auto" = the pt-BR one when the interface is pt-BR.
SkillLanguage = Literal["auto", "en", "pt-BR"]
# How an answer of another conversation reaches the person: "speak" tells the assistant at once
# (it summarizes it aloud); the others hold it in the inbox for "Hear what's ready", after a
# short spoken notice and the answer sound ("announce"), the sound only, or nothing.
AnswerDelivery = Literal["speak", "announce", "sound", "silent"]
# Where the assistant's personality comes from: nothing, a Markdown file of the person's (for
# example an Obsidian note), or a text written in the Assistant tab and kept by VoiceMate.
PersonalitySource = Literal["none", "file", "custom"]
# The companion's own hotkey actions for the assistant (not engine flows): they share the
# hotkey table, the conflict checks and the Hotkeys tab with the flows.
AssistantHotkeyAction = Literal[
    "assistant_talk", "assistant_window", "assistant_mode", "assistant_back", "assistant_digest"
]
ASSISTANT_HOTKEY_ACTIONS: tuple[AssistantHotkeyAction, ...] = get_args(AssistantHotkeyAction)

# "answer": another conversation answered and the answer waits in the assistant's inbox.
CueName = Literal["start", "transcribing", "ready", "ai_ready", "warning", "error", "answer"]
CueSource = Literal["preset", "file"]
CuePreset = Literal["classic", "soft", "click"]
CUE_NAMES: tuple[CueName, ...] = get_args(CueName)

HotkeyCheck = Literal["ok", "in_use", "duplicate", "invalid", "engine_owned"]
CompanionCommand = Literal["show", "quit", "restart-engine", "restart-wsl", "settings"]
NotificationLevel = Literal["info", "warning", "error"]
# What clicking the notification does (tray balloons have no buttons; one click target).
NotificationAction = Literal["none", "show_status", "open_logs", "open_settings"]

SETTINGS_VERSION: Final = 1


@dataclass(frozen=True)
class CueSettings:
    enabled: bool = True
    source: CueSource = "preset"
    preset: CuePreset = "classic"
    file: str = ""  # absolute path of a PCM 8/16-bit .wav when source == "file"
    volume: float = 1.0  # 0.0 .. 1.0, multiplied by the master volume


@dataclass(frozen=True)
class AssistantSettings:
    """The `[assistant]` table. The assistant is off until the person turns it on."""

    mode: AssistantMode = "off"
    model: AssistantModel = "haiku"
    effort: AssistantEffort = "low"
    voice_commands: bool = True
    expressions: ExpressionsMode = "off"
    user_name: str = ""
    personality_source: PersonalitySource = "none"
    personality_file: str = ""  # source "file": a .md (for example in an Obsidian vault); the skill reads it
    obsidian_vault: str = ""  # the folder of the person's notes vault (assistant.json "notes_vault")
    # Mode "desktop": the exact sidebar title of the Desktop conversation used as the central one
    # ("" = none chosen: the assistant does not start and says why).
    desktop_conversation: str = ""
    permission_mode: AssistantPermission = "acceptEdits"
    skill_language: SkillLanguage = "auto"
    custom_skill_dir: str = ""  # a folder with a SKILL.md that replaces the packaged skill
    answer_delivery: AnswerDelivery = "announce"
    # The assistant's voice: replies are spoken by the engine's voice (its `/tts` endpoint) and
    # `voice` is one of the voice ids the engine lists ("" = the engine's default voice).
    speak_replies: bool = True
    voice: str = ""
    # What `start_conversation` gives a new conversation when the central names no model or effort.
    new_conversation_model: AssistantModel = "sonnet"
    new_conversation_effort: AssistantEffort = "medium"
    # The Claude Desktop profile (how the Desktop's controls are found): check for a newer one
    # at start, and an imported file that replaces it ("" = the downloaded or packaged one).
    desktop_profile_auto_update: bool = True
    desktop_profile_override: str = ""
    # The assistant's hotkeys ("" = none). Kept here, not in `CompanionSettings.hotkeys`, so a
    # settings file saved before they existed gets the defaults.
    talk_hotkey: str = "ctrl+alt+s"
    window_hotkey: str = "ctrl+alt+w"
    mode_hotkey: str = ""
    back_hotkey: str = ""
    digest_hotkey: str = "ctrl+alt+r"

    def hotkey(self, action: AssistantHotkeyAction) -> str:
        return {
            "assistant_talk": self.talk_hotkey,
            "assistant_window": self.window_hotkey,
            "assistant_mode": self.mode_hotkey,
            "assistant_back": self.back_hotkey,
            "assistant_digest": self.digest_hotkey,
        }[action]

    def with_hotkeys(self, chords: Mapping[str, str]) -> AssistantSettings:
        """A copy with the actions in `chords` rebound ("" clears one); other keys are ignored."""
        current = {action: self.hotkey(action) for action in ASSISTANT_HOTKEY_ACTIONS}
        current.update({action: chords[action] for action in ASSISTANT_HOTKEY_ACTIONS if action in chords})
        return replace(
            self,
            talk_hotkey=current["assistant_talk"],
            window_hotkey=current["assistant_window"],
            mode_hotkey=current["assistant_mode"],
            back_hotkey=current["assistant_back"],
            digest_hotkey=current["assistant_digest"],
        )


def _default_cues() -> dict[CueName, CueSettings]:
    return {name: CueSettings() for name in CUE_NAMES}


def default_engine_mode() -> EngineMode:
    return "wsl2" if sys.platform == "win32" else "local"


@dataclass(frozen=True)
class FlowEntry:
    """A flow the engine serves (from /health flow_info)."""

    name: str
    kind: FlowKind
    engine_chord: str  # the engine's own chord (shown read-only where the engine owns hotkeys)


@dataclass(frozen=True)
class RecentItem:
    """A result kept by the companion, with the daemon instance that produced it.

    Only shallowly immutable (`record` is a dict) and not hashable: do not use it as a key.
    """

    instance: str
    record: ResultRecord


@dataclass(frozen=True)
class HotkeyBinding:
    """`chord` is never empty: a flow without a binding has no hotkey."""

    flow: str  # engine flow NAME (an identifier from the engine config, see FlowInfo)
    chord: str  # e.g. "ctrl+alt+v" (the engine's hotkey format)


@dataclass(frozen=True)
class CompanionSettings:
    version: int = SETTINGS_VERSION
    client_key: str = ""  # generated on first run, persisted (lease takeover after a crash)
    language: UiLanguage = "auto"  # auto = OS UI language, decided at startup
    # Language of the dictation (and of Claude's spoken answers): passed to the engine it
    # starts (`--transcription-language` and `--output-lang`), so changing it restarts it.
    dictation_language: DictationLanguage = "interface"
    engine_mode: EngineMode = field(default_factory=default_engine_mode)
    wsl_distro: str = ""  # "" = WSL default distro
    engine_dir: str = ""  # relative to $HOME (or absolute); "" = detect/ask on first run
    daemon_port: int = 47821
    # Only flows the engine reports in flow_info get a hotkey; others are kept but idle.
    hotkeys: tuple[HotkeyBinding, ...] = (
        HotkeyBinding("clipboard", "ctrl+alt+v"),
        HotkeyBinding("claude_chat", "ctrl+alt+a"),
    )
    cues_enabled: bool = True
    master_volume: float = 0.8
    cues: Mapping[CueName, CueSettings] = field(default_factory=_default_cues, hash=False)
    wsl_restart_policy: WslRestartPolicy = "auto"
    notify_level: NotifyLevel = "warnings"
    start_at_login: bool = False
    # Windows 11: keep the tray icon on the taskbar instead of the overflow behind the
    # arrow next to the clock (no effect elsewhere).
    tray_icon_visible: bool = True
    # Where the engine this app starts transcribes ("auto" = the engine's own choice) and
    # the Whisper model of each mode (`--stt-mode` and `--model`); a change restarts it.
    stt_mode: SttMode = "auto"
    stt_cpu_model: SttModel = DEFAULT_STT_MODEL
    stt_gpu_model: SttModel = DEFAULT_STT_MODEL
    # Spoken answers (`--tts-engine external --tts-url ... --tts-external-voice ...`; the token
    # travels in the VOICEMATE_TTS_TOKEN environment variable, never on the command line).
    # With "default" the other three are kept but unused; a change restarts the engine.
    tts_engine: TtsChoice = "default"
    tts_url: str = DEFAULT_TTS_URL
    tts_voice: str = ""  # a provider voice id; "" = the provider's default
    tts_token: str = field(default="", repr=False)
    assistant: AssistantSettings = field(default_factory=AssistantSettings)


def assistant_hotkeys(settings: CompanionSettings) -> tuple[HotkeyBinding, ...]:
    """The assistant's bound hotkeys, as bindings named after their action."""
    assistant = settings.assistant
    return tuple(
        HotkeyBinding(action, assistant.hotkey(action))
        for action in ASSISTANT_HOTKEY_ACTIONS
        if assistant.hotkey(action)
    )


def all_hotkeys(settings: CompanionSettings) -> tuple[HotkeyBinding, ...]:
    """The flow bindings and the assistant's: what the conflict checks look at. The assistant's
    only count while it is on: they are not held otherwise, so an old shortcut that uses the
    same keys must not block saving an unrelated setting."""
    if settings.assistant.mode == "off":
        return settings.hotkeys
    return settings.hotkeys + assistant_hotkeys(settings)


@dataclass(frozen=True)
class TtsProbe:
    """The outcome of talking to a TTS provider from the settings window. `message` is
    localized and fit to show; `voices` is what the provider offers (empty when it failed)."""

    ok: bool
    message: str
    provider: str = ""
    voices: tuple[Voice, ...] = ()


@dataclass(frozen=True)
class CompanionSnapshot:
    """Everything the UI renders. Immutable: the controller publishes a new one per change."""

    rev: int = 0
    tray_state: TrayState = "stopped"
    supervisor: SupervisorState = "stopped"
    engine_ready: bool = False
    engine_outdated: bool = False  # the daemon speaks API < 2: restart/update the engine
    audio: AudioHealth = "unknown"
    mic_count: int = -1  # active capture devices on this OS; -1 = unknown
    flow: str | None = None  # flow of the current operation (see StateData.flow)
    instance: str = ""  # current daemon instance
    flows: tuple[FlowEntry, ...] = ()  # flows the engine serves (from /health flow_info)
    hotkeys_owned_by_engine: bool = False  # Linux local mode: hotkey page is read-only
    recording_since: float | None = None  # time.time() (client clock) when recording started
    last_text_snippet: str = ""  # last delivered text, shortened
    status_text: str = ""  # one localized line for the tooltip/status window
    detail: str = ""  # optional localized detail (last error, restart count...)
    restarts: int = 0  # engine/WSL restarts in the current circuit-breaker window
    pending_unacked: int = 0  # old results never delivered: offer them in the UI
    pending_wsl_restart: bool = False  # policy "ask": waiting for the user's answer
    hotkeys_suspended: bool = False
    stt: SttInfo | None = None  # the transcription engine running (from /health), None until known


@dataclass(frozen=True)
class Notification:
    level: NotificationLevel
    title: str
    message: str
    action: NotificationAction = "none"


SnapshotListener = Callable[[CompanionSnapshot], None]
NotificationListener = Callable[[Notification], None]
Unsubscribe = Callable[[], None]


class CompanionController(Protocol):
    """Startup order: `main.py` constructs the controller, reads `settings()` (valid before
    `start()`), calls `app.i18n.set_language`, builds the UI, then calls `start()`. The core
    produces no user-facing strings before `start()`."""

    def start(self) -> None:
        """Start supervision (spawn or attach the engine), hotkeys and event polling."""

    def snapshot(self) -> CompanionSnapshot: ...

    def subscribe(self, listener: SnapshotListener) -> Unsubscribe:
        """Delivers the current snapshot at once, then every change (dispatcher thread)."""

    def subscribe_notifications(self, listener: NotificationListener) -> Unsubscribe:
        """Each user-facing notification, already filtered by notify_level (dispatcher thread)."""

    def toggle(self, flow: str) -> None:
        """Same as pressing that flow's hotkey. While the engine starts: a notification, no trigger."""

    def cancel(self) -> None: ...

    def restart_engine(self) -> None:
        """Graceful: /shutdown, close stdin, then kill; resets the circuit breaker."""

    def restart_wsl(self) -> None:
        """`wsl --shutdown` + respawn (wsl2 mode only); resets the circuit breaker."""

    def answer_wsl_restart(self, approved: bool) -> None:
        """Answer to a pending WSL restart (policy "ask", see snapshot.pending_wsl_restart)."""

    def quit(self, on_done: Callable[[], None]) -> None:
        """Non-blocking. Releases leases (/unregister), stops what this app started (graceful
        /shutdown, then stdin EOF, then kill; an attached daemon is left running), unregisters
        hotkeys, then calls `on_done` from a controller thread (hard cap 15 s)."""

    def settings(self) -> CompanionSettings:
        """The latest applied, normalized settings (cached in memory, no I/O). The UI reads
        it each time it needs a value and never keeps its own copy: the core changes
        settings too (client_key, a detected engine_dir, the OS autostart state)."""

    def apply_settings(self, settings: CompanionSettings) -> list[str]:
        """Validate, persist and apply. Returns localized errors; empty list = applied."""

    def check_hotkey(self, chord: str, flow: str) -> HotkeyCheck:
        """Would `chord` work for `flow`? Format, duplicate with another flow, taken by another
        app (our own registration is not "in_use"); "engine_owned" in Linux local mode."""

    def suspend_hotkeys(self, suspended: bool) -> None:
        """Release the global hotkeys while the UI captures a new chord."""

    def preview_cue(self, cue: CueName, settings: CueSettings, master_volume: float) -> None:
        """Play `cue` once with these (possibly unsaved) settings at `master_volume` x
        `settings.volume`, also when cues are muted or this cue is disabled: the settings
        window previews what the user is editing, before it is applied."""

    def recent_results(self) -> list[RecentItem]:
        """Cached (no I/O): the last 10 delivered/seen results, newest first."""

    def pending_results(self) -> list[RecentItem]:
        """Cached (no I/O): results never delivered (any instance), oldest first."""

    def copy_result(self, instance: str, result_seq: int) -> None:
        """Put a recent/pending result on the clipboard (verified) and ACK it `delivered`
        (last status wins, also over an earlier `dismissed`)."""

    def clear_pending(self, keys: Sequence[tuple[str, int]]) -> None:
        """Remove these `(instance, result_seq)` items from the Not copied list (memory and
        disk): the ones the user saw when confirming, so an item that arrived meanwhile
        stays. Nothing is ACKed again (they were ACKed when they became pending), except a
        restored item whose ACK the daemon never got. The UI confirms first."""

    def open_logs(self) -> None: ...

    def tts_voices(self, settings: CompanionSettings) -> TtsProbe:
        """BLOCKING (call it off the GUI thread): ask the provider of `settings` for its voices
        (`GET /v1/info`), straight from this computer."""

    def test_tts(self, settings: CompanionSettings) -> TtsProbe:
        """BLOCKING: have the provider of `settings` speak a short sentence and play it."""

    def assistant_voices(self) -> TtsProbe:
        """BLOCKING: the voices of the engine's voice (`<engine>/tts/v1/info`), for the Assistant tab."""

    def test_assistant_voice(self, voice: str) -> TtsProbe:
        """BLOCKING: say a short sentence with `voice` ("" = the engine's default) the way the
        assistant speaks, through the engine."""

    def check_stt(self, settings: CompanionSettings) -> SttCheckReport | None:
        """BLOCKING (call it off the GUI thread): which transcription modes the engine of
        `settings` has installed, for these models (`make stt-check`, through WSL on Windows).
        None when it cannot tell (external mode, no engine folder, the check failed)."""

    def install_stt(self, settings: CompanionSettings, mode: SttDevice, on_line: Callable[[str], None]) -> bool:
        """BLOCKING, minutes long: install what `mode` needs for its model in `settings`
        (`make stt-install`). `on_line` gets each output line on the calling thread.
        True when the mode is ready afterwards. The caller asks the user first."""

    def assistant(self) -> AssistantService:
        """The voice assistant's service (always there; with the mode "off" it does nothing).
        It publishes its own snapshots, from its own worker thread."""
