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
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol, get_args

from app.protocol.models import AudioHealth, FlowKind, ResultRecord

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
UiLanguage = Literal["auto", "pt-BR", "en", "es"]
NotifyLevel = Literal["all", "warnings", "errors", "none"]

CueName = Literal["start", "transcribing", "ready", "ai_ready", "warning", "error"]
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
