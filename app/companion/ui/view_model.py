"""Small pure helpers that turn a snapshot plus the settings into what the views show."""

from __future__ import annotations

from typing import Final, get_args

from app.companion.contract import SETTINGS_VERSION, CompanionSettings, CompanionSnapshot, FlowEntry, TrayState
from app.protocol.models import FlowKind

# A recording is in progress: the flow actions become "Stop and ...".
RECORDING_STATES: Final[frozenset[TrayState]] = frozenset({"recording"})
# Something can be cancelled (a recording, or processing that has not delivered yet).
CANCELLABLE_STATES: Final[frozenset[TrayState]] = frozenset({"recording", "transcribing", "thinking", "speaking"})
_FLOW_KINDS: Final[tuple[FlowKind, ...]] = get_args(FlowKind)


def displayed_flows(snapshot: CompanionSnapshot, settings: CompanionSettings) -> tuple[FlowEntry, ...]:
    """The engine's flows. Before the engine reports them: the default flows (named after
    their kind) plus any saved binding of such a flow, so the menu and the settings never
    look empty, and a flow whose shortcut was cleared can still get a new one."""
    if snapshot.flows:
        return snapshot.flows
    names = [binding.flow for binding in CompanionSettings().hotkeys]
    names += [binding.flow for binding in settings.hotkeys if binding.flow not in names]
    flows: list[FlowEntry] = []
    for name in names:
        if name in _FLOW_KINDS:
            kind: FlowKind = "claude_chat" if name == "claude_chat" else "clipboard"
            flows.append(FlowEntry(name=name, kind=kind, engine_chord=""))
    return tuple(flows)


def settings_read_only(settings: CompanionSettings) -> bool:
    """Saved by a newer VoiceMate: shown, never written (the core refuses to apply)."""
    return settings.version > SETTINGS_VERSION


def chord_for(flow: FlowEntry, snapshot: CompanionSnapshot, settings: CompanionSettings) -> str:
    """The chord that triggers `flow`: the engine's own where it owns hotkeys, else ours."""
    if snapshot.hotkeys_owned_by_engine:
        return flow.engine_chord
    for binding in settings.hotkeys:
        if binding.flow == flow.name:
            return binding.chord
    return ""


def idle_chord(snapshot: CompanionSnapshot, settings: CompanionSettings) -> str:
    """The chord named in the idle tooltip ("Listening for Ctrl+Alt+V"): the dictation flow's."""
    flows = displayed_flows(snapshot, settings)
    ordered = sorted(flows, key=lambda flow: flow.kind != "clipboard")
    for flow in ordered:
        chord = chord_for(flow, snapshot, settings)
        if chord:
            return chord
    return ""


def can_trigger(snapshot: CompanionSnapshot) -> bool:
    """Flow actions are offered once the engine is ready (the controller would only notify)."""
    return snapshot.engine_ready and snapshot.tray_state not in ("stopped", "starting", "restarting", "error")
