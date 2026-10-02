"""The companion's view model: a PURE reducer from inputs to state, cues and notifications.

`reduce(state, input, now)` never does I/O; the controller owns the only `CoreState`
(on its dispatcher thread) and executes the returned `Effects`. `to_snapshot` renders the
`CompanionSnapshot` the UI shows. The reaction tables of docs/companion-app.md ("Tray
states, cues and reactions", "Notifications") live here:

- an event's own cue REPLACES the cue on entering a tray state (never two cues);
- cues for daemon events play only when the event's `needs_cue` is true; companion-local
  cues (delivery failed, supervisor or health driven tray states) always play; rendering
  a snapshot never plays a cue;
- `ready` lasts READY_S after a final result reached the clipboard;
- notifications: once per code per 5 minutes, except "not copied", which is aggregated
  instead of dropped; `slow_backend` once per session; filtered by `notify_level`
  (one-time tips such as `pin_taskbar` only by `none`).
All user-facing text is built here from codes with `app.i18n._` (never the daemon's
`message`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Final, Literal

from app.companion.chords import display_chord
from app.companion.contract import (
    CompanionSettings,
    CompanionSnapshot,
    CueName,
    EngineMode,
    FlowEntry,
    HotkeyBinding,
    Notification,
    NotificationAction,
    NotificationLevel,
    NotifyLevel,
    SupervisorState,
    TrayState,
)
from app.companion.delivery import DeliveryJob
from app.companion.supervisor.policy import FailureReason, RestartReason
from app.i18n import _
from app.protocol.models import (
    API_VERSION,
    AudioHealth,
    Event,
    FlowKind,
    HealthPayload,
    Phase,
    ResultData,
    SessionState,
    SnapshotData,
    StateData,
    WarningCode,
)

READY_S: Final = 3.0
NOTIFY_INTERVAL_S: Final = 300.0
SNIPPET_CHARS: Final = 60

NoticeCode = Literal[
    "engine_starting",
    "engine_stopped",
    "engine_failed",
    "wsl_failed",
    "engine_dir_missing",
    "spawn_failed",
    "wsl_restarting",
    "wsl_restart_ask",
    "wsl_restart_ask_others",
    "engine_outdated",
    "systemd_attached",
    "auth_failed",
    "settings_reset",
    "trigger_offline",
    "trigger_timeout",
    "trigger_rejected",
    "trigger_error",
    "no_speech",
    "no_audio",
    "slow_backend",
    "mic_unavailable",
    "transcription_failed",
    "chat_failed",
    "audio_down",
    "no_mic",
    "not_copied",
    "copy_failed",
    "hotkey_in_use",
    "pin_taskbar",
]
# Why the tray shows `warning` while the supervisor is healthy.
WarningReason = Literal["no_mic", "audio_down", "mic_unavailable", "mic_blocked"]

_LEVEL_RANK: Final[dict[NotificationLevel, int]] = {"info": 0, "warning": 1, "error": 2}
_MIN_RANK: Final[dict[NotifyLevel, int]] = {"all": 0, "warnings": 1, "errors": 2, "none": 3}
# One-time tips: info level, yet shown unless notifications are off ("warnings", the
# default, would otherwise hide them on the very first run).
_TIPS: Final[frozenset[NoticeCode]] = frozenset({"pin_taskbar"})
_ENTER_CUE: Final[dict[TrayState, CueName]] = {"transcribing": "transcribing", "warning": "warning", "error": "error"}
_WARNING_NOTICES: Final[dict[WarningCode, NoticeCode]] = {
    "no_speech": "no_speech",
    "no_audio": "no_audio",
    "slow_backend": "slow_backend",
}
_FLOW_KINDS: Final[tuple[FlowKind, ...]] = ("clipboard", "claude_chat")
# Tray states of an operation: leaving one back into the same warning is not a new warning.
_OPERATION_TRAYS: Final[frozenset[TrayState]] = frozenset(
    {"recording", "transcribing", "thinking", "speaking", "ready"}
)


@dataclass(frozen=True)
class Now:
    mono: float  # time.monotonic(): durations
    wall: float  # time.time(): recording_since (client clock, never compared with the daemon's)


@dataclass(frozen=True)
class EngineView:
    state: SessionState = "idle"
    phase: Phase | None = None
    flow: str | None = None
    op_seq: int = 0
    mic_live: bool = False


@dataclass(frozen=True)
class CoreState:
    started: bool = False
    mode: EngineMode = "wsl2"
    port: int = 47821
    notify_level: NotifyLevel = "warnings"
    hotkeys: tuple[HotkeyBinding, ...] = ()
    hotkeys_owned_by_engine: bool = False
    hotkeys_suspended: bool = False
    supervisor: SupervisorState = "stopped"
    attempt: int = 0
    restart_reason: RestartReason | None = None
    failure: FailureReason | None = None
    restarts: int = 0
    pending_wsl_restart: bool = False
    engine_ready: bool = False
    engine_outdated: bool = False
    instance: str = ""
    flows: tuple[FlowEntry, ...] = ()
    audio: AudioHealth = "unknown"
    mic_count: int = -1
    engine: EngineView = EngineView()
    recording_since: float | None = None
    ready_until: float | None = None
    warning: WarningReason | None = None
    last_text_snippet: str = ""
    pending_unacked: int = 0
    notified_at: Mapping[NoticeCode, float] = field(default_factory=dict)
    not_copied_since: float | None = None
    not_copied_count: int = 0
    slow_backend_notified: bool = False


# --- inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Started:
    pass


@dataclass(frozen=True)
class SettingsSeen:
    settings: CompanionSettings
    hotkeys_owned_by_engine: bool


@dataclass(frozen=True)
class SupervisorUpdate:
    state: SupervisorState
    attempt: int
    reason: RestartReason | None
    failure: FailureReason | None
    restarts: int
    pending_wsl_restart: bool


@dataclass(frozen=True)
class HealthSeen:
    payload: HealthPayload


@dataclass(frozen=True)
class SnapshotSeen:
    """`RegisterResponse.snapshot` or a `reset`: render it, never cue."""

    instance: str
    data: SnapshotData


@dataclass(frozen=True)
class EventSeen:
    """A live `/events` event (`result` events go through `ResultSeen` / delivery)."""

    event: Event


@dataclass(frozen=True)
class ResultSeen:
    """A result the companion does NOT deliver (`needs_delivery` false)."""

    data: ResultData


@dataclass(frozen=True)
class DeliveryOutcome:
    job: DeliveryJob
    ok: bool


@dataclass(frozen=True)
class NotCopiedSeen:
    count: int


@dataclass(frozen=True)
class MicCountSeen:
    count: int


@dataclass(frozen=True)
class PendingCount:
    count: int


@dataclass(frozen=True)
class HotkeysSuspendedSeen:
    suspended: bool


@dataclass(frozen=True)
class NoticeRequested:
    code: NoticeCode
    names: tuple[str, ...] = ()
    status: int = 0


@dataclass(frozen=True)
class Tick:
    pass


ModelInput = (
    Started
    | SettingsSeen
    | SupervisorUpdate
    | HealthSeen
    | SnapshotSeen
    | EventSeen
    | ResultSeen
    | DeliveryOutcome
    | NotCopiedSeen
    | MicCountSeen
    | PendingCount
    | HotkeysSuspendedSeen
    | NoticeRequested
    | Tick
)


@dataclass(frozen=True)
class Effects:
    cue: CueName | None = None
    notifications: tuple[Notification, ...] = ()


@dataclass(frozen=True)
class _Note:
    code: NoticeCode
    names: tuple[str, ...] = ()
    status: int = 0
    count: int = 1


class _Unset:
    pass


_UNSET: Final = _Unset()


class _Step:
    def __init__(self, state: CoreState) -> None:
        self.state = state
        self.cue: CueName | None | _Unset = _UNSET  # explicit cue of the event (None = no cue)
        self.cue_allowed = True  # daemon events: their needs_cue
        self.notes: list[_Note] = []

    def set(self, **changes: object) -> None:
        self.state = replace(self.state, **changes)  # type: ignore[arg-type]

    def note(self, code: NoticeCode, *, names: tuple[str, ...] = (), status: int = 0, count: int = 1) -> None:
        self.notes.append(_Note(code, names, status, count))


# --- reducer --------------------------------------------------------------------------------


def reduce(state: CoreState, message: ModelInput, now: Now) -> tuple[CoreState, Effects]:
    before = tray_state(state, now.mono)
    step = _Step(state)
    _apply(step, message, now)
    after = tray_state(step.state, now.mono)
    if isinstance(step.cue, _Unset):
        cue = _ENTER_CUE.get(after) if after != before else None
        if cue == "warning" and before in _OPERATION_TRAYS and _warning_cause(state) == _warning_cause(step.state):
            cue = None  # the same warning shows again because an operation ended: no repeat
    else:
        cue = step.cue
    if not step.cue_allowed or not step.state.started:
        cue = None
    notifications: list[Notification] = []
    new_state = step.state
    for note in step.notes:
        new_state, notification = _notify(new_state, note, now.mono)
        if notification is not None:
            notifications.append(notification)
    return new_state, Effects(cue, tuple(notifications))


def _warning_cause(state: CoreState) -> tuple[WarningReason | None, bool, bool]:
    return state.warning, state.supervisor == "degraded", state.engine_outdated


def _apply(step: _Step, message: ModelInput, now: Now) -> None:
    s = step.state
    if isinstance(message, Started):
        step.cue_allowed = False
        step.set(started=True)
    elif isinstance(message, SettingsSeen):
        step.cue_allowed = False
        settings = message.settings
        step.set(
            mode=settings.engine_mode,
            port=settings.daemon_port,
            notify_level=settings.notify_level,
            hotkeys=settings.hotkeys,
            hotkeys_owned_by_engine=message.hotkeys_owned_by_engine,
        )
    elif isinstance(message, SupervisorUpdate):
        _supervisor(step, message)
    elif isinstance(message, HealthSeen):
        _health(step, message.payload)
    elif isinstance(message, SnapshotSeen):
        step.cue_allowed = False
        _engine_state(step, message.data["state"], now)
        _audio(step, message.data["audio"])
        step.set(instance=message.instance)
    elif isinstance(message, EventSeen):
        _event(step, message.event, now)
    elif isinstance(message, ResultSeen):
        data = message.data
        step.cue_allowed = data["needs_cue"]
        step.cue = None
        if data["final"]:
            step.set(ready_until=now.mono + READY_S, last_text_snippet=snippet(data["text"]))
            step.cue = _ready_cue(data["kind"] == "ai_response", data["spoken"])
    elif isinstance(message, DeliveryOutcome):
        _delivery(step, message, now)
    elif isinstance(message, NotCopiedSeen):
        step.cue = None
        if message.count > 0:
            step.note("not_copied", count=message.count)
    elif isinstance(message, MicCountSeen):
        _mic_count(step, message.count)
    elif isinstance(message, PendingCount):
        step.set(pending_unacked=message.count)
    elif isinstance(message, HotkeysSuspendedSeen):
        step.set(hotkeys_suspended=message.suspended)
    elif isinstance(message, NoticeRequested):
        step.cue = None
        step.note(message.code, names=message.names, status=message.status)
    elif isinstance(message, Tick):
        step.cue_allowed = False
        if s.ready_until is not None and now.mono >= s.ready_until:
            step.set(ready_until=None)


def _supervisor(step: _Step, update: SupervisorUpdate) -> None:
    changes: dict[str, object] = {
        "supervisor": update.state,
        "attempt": update.attempt,
        "restart_reason": update.reason,
        "failure": update.failure,
        "restarts": update.restarts,
        "pending_wsl_restart": update.pending_wsl_restart,
    }
    if update.state not in ("healthy", "degraded"):
        # The engine is gone or loading: whatever it was doing is over.
        changes.update(engine_ready=False, engine=EngineView(), recording_since=None, ready_until=None)
    step.set(**changes)


def _health(step: _Step, payload: HealthPayload) -> None:
    api_version = payload.get("api_version")  # absent on a v1 daemon
    outdated = not isinstance(api_version, int) or isinstance(api_version, bool) or api_version < API_VERSION
    if outdated and not step.state.engine_outdated:
        step.note("engine_outdated")
    flows: list[FlowEntry] = []
    for info in payload.get("flow_info", []):
        if not isinstance(info, dict) or not info.get("name"):
            continue
        kind: FlowKind = next((option for option in _FLOW_KINDS if option == info.get("kind")), "clipboard")
        flows.append(FlowEntry(str(info["name"]), kind, str(info.get("hotkey", ""))))
    step.set(
        engine_ready=bool(payload.get("ready", False)) and not outdated,
        engine_outdated=outdated,
        instance=str(payload.get("instance", "")),
        flows=tuple(flows) or step.state.flows,
    )
    audio = payload.get("audio", "unknown")
    if audio in ("ok", "down", "unknown"):
        _audio(step, audio)


def _audio(step: _Step, audio: AudioHealth) -> None:
    s = step.state
    if audio == s.audio:
        return
    step.set(audio=audio)
    if audio == "down":
        if s.warning != "no_mic":
            step.set(warning="audio_down")
        step.note("audio_down")
    elif audio == "ok" and s.warning is not None and s.mic_count != 0:
        # Clears when audio is back to ok and the OS has a microphone.
        step.set(warning=None)


def _mic_count(step: _Step, count: int) -> None:
    s = step.state
    if count == s.mic_count:
        return
    step.set(mic_count=count)
    if count == 0:
        step.set(warning="no_mic")
        step.note("no_mic")
    elif s.mic_count == 0 and s.warning == "no_mic":
        step.set(warning="audio_down" if s.audio == "down" else None)


def _engine_state(step: _Step, data: StateData, now: Now) -> EngineView:
    previous = step.state.engine
    state = data["state"]
    view = EngineView(
        state=state,
        phase=data.get("phase") if state == "processing" else None,
        flow=data.get("flow"),
        op_seq=data.get("op_seq", 0),
        mic_live=bool(data.get("mic_live")) and state == "recording",
    )
    recording_since = step.state.recording_since
    if state == "recording":
        if previous.state != "recording" or recording_since is None:
            recording_since = now.wall
    else:
        recording_since = None
    step.set(engine=view, recording_since=recording_since)
    return previous


def _event(step: _Step, event: Event, now: Now) -> None:
    if event["type"] == "state":
        data = event["data"]
        step.cue_allowed = data["needs_cue"]
        previous = _engine_state(step, data, now)
        if step.state.engine.mic_live and not previous.mic_live:
            step.cue = "start"  # "speak now": the capture stream is open
            step.set(warning=None)
    elif event["type"] == "error":
        error = event["data"]
        step.cue_allowed = error["needs_cue"]
        step.cue = "error"
        if error["code"] == "mic_unavailable":
            s = step.state
            if s.mic_count == 0:
                reason: WarningReason = "no_mic"
            elif s.audio == "ok":
                reason = "mic_blocked"
            else:
                reason = "mic_unavailable"
            step.set(warning=reason)
            step.note("mic_unavailable")
        elif error["code"] == "chat_failed":
            step.note("chat_failed")
        else:
            step.note("transcription_failed")
    elif event["type"] == "warning":
        warning = event["data"]
        step.cue_allowed = warning["needs_cue"]
        code = warning["code"]
        step.cue = "warning" if code == "time_limit_soon" else None
        notice = _WARNING_NOTICES.get(code)
        if notice is not None:
            step.note(notice)
    elif event["type"] == "health":
        _audio(step, event["data"]["audio"])
    elif event["type"] == "snapshot":
        step.cue_allowed = False
        _engine_state(step, event["data"]["state"], now)
        _audio(step, event["data"]["audio"])
    else:  # shutdown and result: the supervisor and the delivery queue handle them
        step.cue = None


def _delivery(step: _Step, outcome: DeliveryOutcome, now: Now) -> None:
    job = outcome.job
    step.cue = None
    if outcome.ok:
        step.set(last_text_snippet=snippet(job.text))
        if job.manual:
            step.set(ready_until=now.mono + READY_S)  # the user's own click: no cue
        elif job.final:
            step.cue_allowed = job.needs_cue
            step.set(ready_until=now.mono + READY_S)
            step.cue = _ready_cue(job.kind == "ai_response", job.spoken)
        return
    # Companion-local failure: its cue always plays.
    step.cue = "error"
    step.note("copy_failed" if job.manual else "not_copied")


def _ready_cue(ai_response: bool, spoken: bool) -> CueName | None:
    if not ai_response:
        return "ready"
    return None if spoken else "ai_ready"


# --- tray and texts --------------------------------------------------------------------------


def tray_state(state: CoreState, mono: float) -> TrayState:
    supervisor = state.supervisor
    if not state.started or supervisor == "stopped":
        return "stopped"
    if supervisor == "starting":
        return "starting"
    if supervisor in ("restarting", "backoff"):
        return "restarting"
    if supervisor == "failed":
        return "error"
    if supervisor == "degraded" or state.engine_outdated:
        return "warning"
    engine = state.engine
    if engine.state == "recording":
        return "recording"
    if engine.state == "processing":
        if engine.phase == "thinking":
            return "thinking"
        if engine.phase == "speaking":
            return "speaking"
        return "transcribing"
    if state.ready_until is not None and mono < state.ready_until:
        return "ready"
    if state.warning is not None:
        return "warning"
    return "idle"


def snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def _primary_chord(state: CoreState) -> str:
    """The hotkey of the main flow (the clipboard one when there is one)."""
    if state.hotkeys_owned_by_engine:
        ordered = sorted(state.flows, key=lambda flow: flow.kind != "clipboard")
        return next((flow.engine_chord for flow in ordered if flow.engine_chord), "")
    flow_names = [flow.name for flow in sorted(state.flows, key=lambda flow: flow.kind != "clipboard")]
    bindings = {binding.flow: binding.chord for binding in state.hotkeys}
    for name in flow_names:
        if name in bindings:
            return bindings[name]
    if not state.flows and state.hotkeys:
        return state.hotkeys[0].chord
    return ""


def _warning_texts(state: CoreState) -> tuple[str, str]:
    wsl = state.mode == "wsl2"
    if state.supervisor == "degraded":
        return _("Engine not responding"), _("No answer from the engine. VoiceMate restarts it if this continues.")
    if state.engine_outdated:
        return _("Engine is outdated"), _("The engine is older than this app. Update it, then use Restart engine.")
    reason = state.warning
    if reason == "no_mic":
        return _("No microphone"), _("Connect a microphone to record.")
    if reason == "audio_down":
        if wsl:
            return _("WSL audio stopped"), _("WSL lost its audio bridge. Restarting WSL brings it back.")
        return _("Audio server is down"), _("The engine cannot reach the audio server.")
    if reason == "mic_blocked":
        return _("Microphone unavailable"), _(
            "Check the privacy settings for microphone access by desktop apps, and close any app that holds the microphone."
        )
    if wsl:
        return _("Microphone unavailable"), _("WSL cannot open the microphone. Restarting WSL fixes it.")
    return _("Microphone unavailable"), _("The engine cannot open the microphone.")


def _restart_detail(reason: RestartReason | None) -> str:
    texts: dict[RestartReason, str] = {
        "crashed": _("The engine stopped unexpectedly."),
        "unresponsive": _("The engine stopped answering."),
        "start_timeout": _("The engine took too long to start."),
        "audio": _("Restarting WSL to recover the audio."),
        "user": _("Restart requested."),
        "wsl_user": _("Restarting WSL."),
        "external": _("The engine is restarting."),
    }
    return texts[reason] if reason is not None else ""


def _failure_texts(failure: FailureReason | None) -> tuple[str, str]:
    if failure == "no_engine_dir":
        return _("Engine folder not found"), _("Set the engine folder in Settings.")
    if failure == "wsl_breaker":
        return _("Engine failed"), _(
            "Restarting WSL did not help. Check the engine log, then use Restart WSL... in the Engine menu."
        )
    return _("Engine failed"), _("The engine failed too many times. Check the engine log, then use Restart engine.")


def _texts(state: CoreState, tray: TrayState, now: Now) -> tuple[str, str]:
    detail = ""
    if tray == "stopped":
        status = _("VoiceMate is stopped")
    elif tray == "starting":
        status = _("Starting engine...")
        if state.mode == "external":
            detail = _("Waiting for the engine on port {port}.").format(port=state.port)
    elif tray == "restarting":
        status = _("Restarting (attempt {attempt})").format(attempt=max(1, state.attempt))
        detail = _restart_detail(state.restart_reason)
    elif tray == "idle":
        chord = _primary_chord(state)
        status = _("Listening for {hotkey}").format(hotkey=display_chord(chord)) if chord else _("Ready")
    elif tray == "recording":
        elapsed = max(0, int(now.wall - (state.recording_since or now.wall)))
        status = _("Recording {elapsed}").format(elapsed=f"{elapsed // 60:02d}:{elapsed % 60:02d}")
    elif tray == "transcribing":
        status = _("Transcribing...")
    elif tray in ("thinking", "speaking"):
        status = _("Claude is answering...")
    elif tray == "ready":
        status = _("Copied: {text}").format(text=state.last_text_snippet)
    elif tray == "warning":
        status, detail = _warning_texts(state)
    else:
        status, detail = _failure_texts(state.failure)
    if state.pending_wsl_restart:
        detail = _("WSL needs a restart to recover the audio. Use Restart WSL now... in the menu.")
    return status, detail


def to_snapshot(state: CoreState, rev: int, now: Now) -> CompanionSnapshot:
    tray = tray_state(state, now.mono)
    # No user-facing text before start(): the UI may set the language until then.
    status, detail = _texts(state, tray, now) if state.started else ("", "")
    return CompanionSnapshot(
        rev=rev,
        tray_state=tray,
        supervisor=state.supervisor,
        engine_ready=state.engine_ready,
        engine_outdated=state.engine_outdated,
        audio=state.audio,
        mic_count=state.mic_count,
        flow=state.engine.flow if state.engine.state != "idle" else None,
        instance=state.instance,
        flows=state.flows,
        hotkeys_owned_by_engine=state.hotkeys_owned_by_engine,
        recording_since=state.recording_since if state.engine.state == "recording" else None,
        last_text_snippet=state.last_text_snippet,
        status_text=status,
        detail=detail,
        restarts=state.restarts,
        pending_unacked=state.pending_unacked,
        pending_wsl_restart=state.pending_wsl_restart,
        hotkeys_suspended=state.hotkeys_suspended,
    )


# --- notifications ----------------------------------------------------------------------------


def _mic_message(state: CoreState) -> str:
    wsl = state.mode == "wsl2"
    if state.mic_count == 0:
        return _("No microphone is connected. Connect one and try again.")
    if state.audio == "ok":
        if wsl:
            return _(
                "The engine could not open the microphone. Allow desktop apps to use the microphone in the "
                "Windows privacy settings, and close any app that holds it exclusively."
            )
        return _("The engine could not open the microphone. Close any app that holds it exclusively.")
    if not wsl:
        return _("The engine could not open the microphone: the audio server is not responding.")
    if state.mic_count > 0:
        return _(
            "Windows has a microphone, but WSL cannot open it: its audio bridge is stuck. Restarting WSL fixes it."
        )
    return _("WSL cannot open the microphone. If one is connected, restarting WSL fixes it.")


def _build(state: CoreState, note: _Note) -> tuple[NotificationLevel, str, str, NotificationAction]:
    code = note.code
    wsl = state.mode == "wsl2"
    if code == "engine_starting":
        return (
            "warning",
            _("VoiceMate is starting"),
            _("The engine is still loading. Try again in a moment."),
            "show_status",
        )
    if code == "engine_stopped":
        return (
            "warning",
            _("VoiceMate is stopped"),
            _("The engine is not running. Use Restart engine in the menu."),
            "show_status",
        )
    if code == "engine_failed":
        return (
            "error",
            _("VoiceMate stopped retrying"),
            _("The engine failed too many times. Check the engine log, then use Restart engine."),
            "open_logs",
        )
    if code == "wsl_failed":
        return (
            "error",
            _("VoiceMate stopped retrying"),
            _(
                "Restarting WSL did not bring the audio back. Check the engine log, then use Restart WSL... in the Engine menu."
            ),
            "open_logs",
        )
    if code == "engine_dir_missing":
        return (
            "error",
            _("Engine folder not found"),
            _("VoiceMate could not find the engine. Set the engine folder in Settings."),
            "open_settings",
        )
    if code == "spawn_failed":
        return (
            "error",
            _("Could not start the engine"),
            _("Starting the engine failed. VoiceMate will try again; the engine log has the details."),
            "open_logs",
        )
    if code == "wsl_restarting":
        return (
            "info",
            _("Restarting WSL"),
            _("WSL lost its audio, so VoiceMate is restarting it. This takes a few seconds."),
            "show_status",
        )
    if code == "wsl_restart_ask":
        return (
            "warning",
            _("WSL needs a restart"),
            _("WSL lost its audio. Choose Restart WSL now... in the VoiceMate menu."),
            "show_status",
        )
    if code == "wsl_restart_ask_others":
        return (
            "warning",
            _("WSL needs a restart"),
            _(
                "WSL lost its audio, but restarting it also stops {distros}. "
                "Choose Restart WSL now... in the VoiceMate menu when convenient."
            ).format(distros=", ".join(note.names)),
            "show_status",
        )
    if code == "engine_outdated":
        return (
            "warning",
            _("Engine is outdated"),
            _("The engine is older than this app. Update it, then use Restart engine in the menu."),
            "show_status",
        )
    if code == "systemd_attached":
        return (
            "warning",
            _("Engine run by systemd"),
            _(
                "A systemd service runs the engine, so VoiceMate attaches to it. To let VoiceMate manage it, "
                "run `systemctl --user disable --now voicemate` in WSL."
            ),
            "none",
        )
    if code == "auth_failed":
        return (
            "error",
            _("Engine access denied"),
            _(
                "The engine refused VoiceMate's access token. VoiceMate reads "
                "`~/.config/voicemate/api-token` again every 30 seconds; the engine log has the details."
            ),
            "open_logs",
        )
    if code == "settings_reset":
        return (
            "warning",
            _("Settings reset"),
            _(
                "The settings file could not be read, so VoiceMate started with the default settings. "
                "The old file was kept as {path}."
            ).format(path=note.names[0] if note.names else "companion.toml.broken-*"),
            "open_settings",
        )
    if code == "trigger_offline":
        return (
            "warning",
            _("Engine unreachable"),
            _("The engine did not answer. VoiceMate keeps trying to reconnect."),
            "show_status",
        )
    if code == "trigger_timeout":
        return (
            "warning",
            _("Engine busy"),
            _("The engine did not answer in time. It may still act on this key press."),
            "show_status",
        )
    if code == "trigger_rejected":
        return (
            "error",
            _("Request rejected"),
            _("The engine rejected the request (HTTP {status}).").format(status=note.status),
            "open_logs",
        )
    if code == "trigger_error":
        return "error", _("Engine error"), _("The engine reported an internal error. See the engine log."), "open_logs"
    if code == "no_speech":
        return "info", _("Nothing transcribed"), _("No speech was detected in the recording."), "none"
    if code == "no_audio":
        return "info", _("Nothing recorded"), _("The recording captured no audio."), "none"
    if code == "slow_backend":
        return (
            "warning",
            _("Slow transcription"),
            _("Transcription is running slower than usual. The engine log has the details."),
            "open_logs",
        )
    if code == "mic_unavailable":
        return "warning", _("Microphone unavailable"), _mic_message(state), "show_status"
    if code == "transcription_failed":
        return (
            "error",
            _("Transcription failed"),
            _("The engine could not transcribe the recording. See the engine log."),
            "open_logs",
        )
    if code == "chat_failed":
        return "error", _("Claude did not answer"), _("The request to Claude failed. See the engine log."), "open_logs"
    if code == "audio_down":
        if wsl:
            return (
                "warning",
                _("WSL audio stopped"),
                _("The engine cannot reach the WSL audio server, so recording will fail."),
                "show_status",
            )
        return (
            "warning",
            _("Audio server is down"),
            _("The engine cannot reach the audio server, so recording will fail."),
            "show_status",
        )
    if code == "no_mic":
        return (
            "warning",
            _("No microphone"),
            _("No microphone is connected. Recording will fail until you connect one."),
            "none",
        )
    if code == "not_copied":
        if note.count <= 1:
            message = _("A transcription was not copied to the clipboard. Open VoiceMate to copy it.")
        else:
            message = _("{count} transcriptions were not copied to the clipboard. Open VoiceMate to copy them.").format(
                count=note.count
            )
        return "error", _("Not copied to the clipboard"), message, "show_status"
    if code == "copy_failed":
        return "error", _("Copy failed"), _("The text could not be copied to the clipboard. Try again."), "none"
    if code == "pin_taskbar":
        return (
            "info",
            _("Pin VoiceMate to the taskbar"),
            _("Open Start, search for VoiceMate, right-click it and choose Pin to taskbar."),
            "none",
        )
    # hotkey_in_use
    return (
        "warning",
        _("Hotkey unavailable"),
        _(
            "Already used by another app: {hotkeys}. If the old VoiceMate hotkeys script is running, close it and "
            "remove it from shell:startup, or choose other hotkeys in Settings."
        ).format(hotkeys=", ".join(display_chord(name) for name in note.names)),
        "open_settings",
    )


def _notify(state: CoreState, note: _Note, mono: float) -> tuple[CoreState, Notification | None]:
    if note.code == "not_copied":
        # Never dropped: within the interval the count adds up into one aggregated message.
        if state.not_copied_since is not None and mono - state.not_copied_since < NOTIFY_INTERVAL_S:
            count = state.not_copied_count + note.count
            state = replace(state, not_copied_count=count)
        else:
            count = note.count
            state = replace(state, not_copied_since=mono, not_copied_count=count)
        note = replace(note, count=count)
    elif note.code == "slow_backend":
        if state.slow_backend_notified:
            return state, None
        state = replace(state, slow_backend_notified=True)
    else:
        last = state.notified_at.get(note.code)
        if last is not None and mono - last < NOTIFY_INTERVAL_S:
            return state, None
        state = replace(state, notified_at={**state.notified_at, note.code: mono})
    level, title, message, action = _build(state, note)
    if note.code in _TIPS:
        if state.notify_level == "none":
            return state, None
    elif _LEVEL_RANK[level] < _MIN_RANK[state.notify_level]:
        return state, None
    return state, Notification(level, title, message, action)
