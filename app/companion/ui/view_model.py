"""Small pure helpers that turn a snapshot plus the settings into what the views show."""

from __future__ import annotations

from typing import Final, get_args

from app.companion.contract import CompanionSettings, CompanionSnapshot, FlowEntry, TrayState
from app.protocol.models import FlowKind

# A recording is in progress: the flow actions become "Stop and ...".
RECORDING_STATES: Final[frozenset[TrayState]] = frozenset({"recording"})
# Something can be cancelled (a recording, or processing that has not delivered yet).
CANCELLABLE_STATES: Final[frozenset[TrayState]] = frozenset({"recording", "transcribing", "thinking", "speaking"})
_FLOW_KINDS: Final[tuple[FlowKind, ...]] = get_args(FlowKind)


def displayed_flows(snapshot: CompanionSnapshot, settings: CompanionSettings) -> tuple[FlowEntry, ...]:
    """The engine's flows; before the engine reports them, the flows of the saved hotkeys
    (named after their kind by default), so the menu and the settings never look empty."""
    if snapshot.flows:
        return snapshot.flows
    flows: list[FlowEntry] = []
    for binding in settings.hotkeys:
        if binding.flow in _FLOW_KINDS and all(flow.name != binding.flow for flow in flows):
            kind: FlowKind = "claude_chat" if binding.flow == "claude_chat" else "clipboard"
            flows.append(FlowEntry(name=binding.flow, kind=kind, engine_chord=""))
    return tuple(flows)


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
