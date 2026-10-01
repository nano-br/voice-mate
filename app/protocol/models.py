"""Daemon <-> companion protocol types (API v2), shared by both sides.

Stdlib only: the companion ships alone on Windows (no numpy, torch or app.core),
so this module must stay importable without the engine's dependencies.
docs/companion-app.md describes every endpoint; these are its payload shapes.
"""

from __future__ import annotations

from typing import Literal, TypedDict

API_VERSION = 2

SessionState = Literal["idle", "recording", "processing"]
Phase = Literal["transcribing", "thinking", "speaking"]
# Flow names are identifiers from the engine config (default: equal to the kind).
FlowKind = Literal["clipboard", "claude_chat"]
TriggerExpect = Literal["toggle", "start", "stop"]
TriggerAction = Literal["started", "stopped", "restarted", "noop"]
CancelAction = Literal["cancelled", "noop"]
ShutdownReason = Literal["user_quit", "restart", "supervisor"]

EventType = Literal["snapshot", "state", "result", "error", "warning", "health", "shutdown"]
ResultKind = Literal["transcript", "ai_response"]
ErrorCode = Literal["mic_unavailable", "transcription_failed", "chat_failed"]
WarningCode = Literal["time_limit_soon", "no_speech", "no_audio", "slow_backend"]
AudioHealth = Literal["ok", "down", "unknown"]
PlatformName = Literal["windows", "linux-x11", "linux-wayland", "wsl2"]
TriggerName = Literal["keyboard-hooks", "pynput", "evdev", "socket"]

# Exclusive leases: while a client holds one, the daemon stops doing that job itself.
Capability = Literal["clipboard", "cues"]
# pending: waiting for the clipboard-lease holder; delivered/failed/dismissed: acked;
# daemon: nobody held the lease, the daemon wrote the clipboard itself (never "unacked").
DeliveryStatus = Literal["pending", "delivered", "failed", "dismissed", "daemon"]
AckStatus = Literal["delivered", "failed", "dismissed"]
ResultsQuery = Literal["unacked", "recent"]


class FlowInfo(TypedDict):
    name: str
    kind: FlowKind
    hotkey: str  # engine-side chord, e.g. "ctrl+alt+v" (authoritative where the engine owns hotkeys)


class HealthPayload(TypedDict):
    """`GET /health` (never requires auth). Served from process start, before the model loads."""

    status: Literal["ok"]
    api_version: int
    version: str
    instance: str
    pid: int
    uptime_s: int
    ready: bool  # model loaded and handlers built; until then other endpoints answer 503
    audio: AudioHealth
    lang: str  # catalog the daemon speaks: "pt_BR" | "en" | "es"
    platform: PlatformName
    trigger: TriggerName
    flows: list[str]  # v1 field, kept for the scripts
    flow_info: list[FlowInfo]
    tts: bool  # the claude_chat flow speaks its answers
    auth: bool  # a bearer token is required (all endpoints but /health)


class RegisterRequest(TypedDict, total=False):
    """`POST /register` body; every field optional (an empty body is the v1 call)."""

    name: str
    version: str
    os: str
    client_key: str  # stable per install: re-registering with it takes over its own leases
    capabilities: list[Capability]
    lease_s: int  # clamped to 10..120, default 40


class RegisterResponse(TypedDict):
    client_id: str
    instance: str
    api_version: int
    granted: list[Capability]
    lease_s: int
    cursor: int  # poll /events from here: `snapshot` is the state at this cursor
    snapshot: SnapshotData


class UnregisterRequest(TypedDict):
    """`POST /unregister`: releases the client's leases at once (Quit)."""

    client_id: str


class TriggerRequest(TypedDict, total=False):
    flow: str
    client_id: str
    expect: TriggerExpect


class TriggerResponse(TypedDict):
    ok: bool
    flow: str
    client_id: str | None
    action: TriggerAction
    op_seq: int
    state: SessionState


class CancelRequest(TypedDict, total=False):
    op_seq: int


class CancelResponse(TypedDict):
    action: CancelAction
    state: SessionState


class ShutdownRequest(TypedDict, total=False):
    reason: ShutdownReason


class ErrorResponse(TypedDict, total=False):
    """Any 4xx/5xx body. `error` is English (for logs), never shown to users as is."""

    error: str
    instance: str  # 409 instance mismatch
    flows: list[str]  # 404 unknown flow


class StateData(TypedDict):
    state: SessionState
    phase: Phase | None  # set while processing
    op_seq: int
    # The flow that STARTED the recording while recording; the destination flow (the
    # one whose hotkey stopped it) while processing ("stop decides the destination").
    # Null while idle (so are flow_kind and phase; op_seq/client_id stay those of the
    # last operation).
    flow: str | None
    flow_kind: FlowKind | None
    client_id: str | None
    # The capture stream is open (the engine's own start beep plays at this moment).
    # Only meaningful while recording (false otherwise). A `state` event is published
    # when it turns true; the `start` cue waits for it.
    mic_live: bool
    needs_cue: bool  # the requesting client held the `cues` lease when this was published


class ResultData(TypedDict):
    result_seq: int
    op_seq: int
    kind: ResultKind
    flow: str | None
    text: str
    # True when the requesting client held the clipboard lease when this was published:
    # that client must deliver it and ACK.
    needs_delivery: bool
    # Last result of the operation: the transcript of a clipboard flow, the
    # ai_response of a claude_chat flow (its transcript is an intermediate result).
    final: bool
    spoken: bool  # TTS already spoke it (skip the ai_ready cue)
    needs_cue: bool  # the requesting client held the `cues` lease when this was published


class ErrorData(TypedDict):
    code: ErrorCode
    detail: str  # technical detail (e.g. the PortAudio error), not localized
    message: str  # localized in the DAEMON's language; companions localize by `code`
    op_seq: int
    needs_cue: bool  # the requesting client held the `cues` lease when this was published


class WarningData(TypedDict):
    code: WarningCode
    detail: str
    message: str
    op_seq: int
    needs_cue: bool  # the requesting client held the `cues` lease when this was published


class HealthData(TypedDict):
    audio: AudioHealth


class ShutdownData(TypedDict):
    reason: ShutdownReason


class ResultRecord(TypedDict):
    result_seq: int
    op_seq: int
    kind: ResultKind
    flow: str | None
    text: str
    final: bool
    created_ts: float  # daemon clock; do not compare with the client's clock
    age_s: float  # computed by the daemon at response time
    delivery: DeliveryStatus


class SnapshotData(TypedDict):
    state: StateData
    unacked: list[ResultRecord]
    audio: AudioHealth


class SnapshotEvent(TypedDict):
    seq: int
    ts: float
    type: Literal["snapshot"]
    data: SnapshotData


class StateEvent(TypedDict):
    seq: int
    ts: float
    type: Literal["state"]
    data: StateData


class ResultEvent(TypedDict):
    seq: int
    ts: float
    type: Literal["result"]
    data: ResultData


class ErrorEvent(TypedDict):
    seq: int
    ts: float
    type: Literal["error"]
    data: ErrorData


class WarningEvent(TypedDict):
    seq: int
    ts: float
    type: Literal["warning"]
    data: WarningData


class HealthEvent(TypedDict):
    seq: int
    ts: float
    type: Literal["health"]
    data: HealthData


class ShutdownEvent(TypedDict):
    seq: int
    ts: float
    type: Literal["shutdown"]
    data: ShutdownData


# Tagged union: mypy narrows on `event["type"]`.
Event = SnapshotEvent | StateEvent | ResultEvent | ErrorEvent | WarningEvent | HealthEvent | ShutdownEvent


class EventsResponse(TypedDict):
    """`GET /events?client_id=&instance=&since=&wait=` (410 = unknown client_id: register again).

    `wait` defaults to 0 and is capped at 25; a missing `instance` or `since` gets a reset.
    """

    instance: str
    cursor: int
    reset: bool
    events: list[Event]
    # Leases this client holds after this poll. A requested lease missing here means it
    # expired (e.g. the client stalled): register again with the same client_key.
    leases: list[Capability]


class Ack(TypedDict):
    result_seq: int
    status: AckStatus
    verified: bool


class AckRequest(TypedDict):
    """`POST /results/ack`."""

    client_id: str
    instance: str
    acks: list[Ack]


class AckResponse(TypedDict):
    ok: bool
    unacked: list[int]


class ResultsResponse(TypedDict):
    """`GET /results?state=unacked|recent&limit=&client_id=&instance=`.

    `unacked`: the oldest pending results; `recent`: the newest results; both in
    ascending `result_seq`, at most `limit` (1..100, default 10).
    """

    instance: str
    results: list[ResultRecord]
