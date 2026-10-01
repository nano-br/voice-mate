"""Localized labels shared by the tray, the status window and the settings window.

Every user-facing string of the UI goes through `app.i18n._` at call time (the
language is chosen once at startup, before any widget exists). Labels for
`Literal` options live in dicts built by functions, so they are translated when
read, never at import time.
"""

from __future__ import annotations

import time

from app.companion.contract import (
    CompanionSnapshot,
    CueName,
    CuePreset,
    CueSource,
    EngineMode,
    FlowEntry,
    HotkeyCheck,
    NotifyLevel,
    SupervisorState,
    TrayState,
    UiLanguage,
    WslRestartPolicy,
)
from app.companion.ui.chords import chord_display
from app.i18n import _
from app.protocol.models import AudioHealth, FlowKind

APP_NAME = "VoiceMate"  # product name: never translated
# Windows truncates tray tooltips at 127 characters.
TOOLTIP_LIMIT = 127
SNIPPET_LIMIT = 60


def shorten(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """One line, at most `limit` characters, with "..." when cut."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: max(limit - 3, 1)].rstrip() + "..."


def format_elapsed(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def flow_kind_title(kind: FlowKind) -> str:
    return {"clipboard": _("Dictate"), "claude_chat": _("Ask Claude")}[kind]


def flow_title(flow: FlowEntry, flows: tuple[FlowEntry, ...]) -> str:
    """ "Dictate", or "Dictate (notes)" when several flows share a kind."""
    title = flow_kind_title(flow.kind)
    if sum(1 for other in flows if other.kind == flow.kind) > 1:
        return _("{title} ({flow})").format(title=title, flow=flow.name)
    return title


def flow_stop_title(kind: FlowKind) -> str:
    """What the flow's action does while recording ("stop decides the destination")."""
    return {"clipboard": _("Stop and copy"), "claude_chat": _("Stop and ask Claude")}[kind]


def status_line(snapshot: CompanionSnapshot, idle_chord: str = "", now: float | None = None) -> str:
    """The one-line status for the tooltip, the menu and the status window.

    The core's `status_text` wins, except while recording: the elapsed time changes
    every second, so the UI renders that line itself.
    """
    state = snapshot.tray_state
    if state == "recording":
        if snapshot.recording_since is None:
            return _("Recording")
        elapsed = (time.time() if now is None else now) - snapshot.recording_since
        return _("Recording {elapsed}").format(elapsed=format_elapsed(elapsed))
    if snapshot.status_text:
        return snapshot.status_text
    return _fallback_line(snapshot, state, idle_chord)


def _fallback_line(snapshot: CompanionSnapshot, state: TrayState, idle_chord: str) -> str:
    if state == "stopped":
        return _("VoiceMate is stopped")
    if state == "starting":
        return _("Starting engine...")
    if state == "restarting":
        # `restarts` already counts the restart in progress.
        if snapshot.restarts > 1:
            return _("Restarting (attempt {attempt})").format(attempt=snapshot.restarts)
        return _("Restarting engine...")
    if state == "idle":
        if idle_chord:
            return _("Listening for {chord}").format(chord=chord_display(idle_chord))
        return _("Ready")
    if state == "transcribing":
        return _("Transcribing...")
    if state in ("thinking", "speaking"):
        return _("Claude is answering...")
    if state == "ready":
        if snapshot.last_text_snippet:
            return _("Copied: {text}").format(text=shorten(snapshot.last_text_snippet, 40))
        return _("Copied")
    if state == "warning":
        if snapshot.mic_count == 0:
            return _("No microphone")
        if snapshot.audio == "down":
            return _("WSL audio stopped")
        return _("Check the microphone")
    # error
    return snapshot.detail or _("The engine stopped working")


def tooltip(snapshot: CompanionSnapshot, idle_chord: str = "", now: float | None = None) -> str:
    line = status_line(snapshot, idle_chord, now)
    if snapshot.hotkeys_suspended and snapshot.tray_state == "idle":
        line = _("Shortcuts paused")
    text = f"{APP_NAME}\n{line}"
    return text if len(text) <= TOOLTIP_LIMIT else text[: TOOLTIP_LIMIT - 3] + "..."


def supervisor_labels() -> dict[SupervisorState, str]:
    return {
        "stopped": _("Stopped"),
        "starting": _("Starting"),
        "healthy": _("Running"),
        "degraded": _("Not responding"),
        "restarting": _("Restarting"),
        "backoff": _("Waiting to restart"),
        "failed": _("Failed"),
    }


def audio_labels() -> dict[AudioHealth, str]:
    return {"ok": _("Working"), "down": _("Not working"), "unknown": _("Unknown")}


def mic_count_label(count: int) -> str:
    if count < 0:
        return _("Unknown")
    if count == 0:
        return _("None found")
    return str(count)


def cue_labels() -> dict[CueName, str]:
    return {
        "start": _("Recording started"),
        "transcribing": _("Transcribing"),
        "ready": _("Copied"),
        "ai_ready": _("Claude answered"),
        "warning": _("Warning"),
        "error": _("Error"),
    }


def cue_source_labels() -> dict[CueSource, str]:
    return {"preset": _("Built-in"), "file": _("Custom file")}


def cue_preset_labels() -> dict[CuePreset, str]:
    return {"classic": _("Classic"), "soft": _("Soft"), "click": _("Click")}


def language_labels() -> dict[UiLanguage, str]:
    # Language names are written in their own language, so anyone can find theirs.
    return {
        "auto": _("Automatic (system language)"),
        "pt-BR": "Português (Brasil)",
        "en": "English",
        "es": "Español",
    }


def engine_mode_labels() -> dict[EngineMode, str]:
    return {
        "wsl2": _("Start it in WSL"),
        "local": _("Start it on this computer"),
        "external": _("Connect only"),
    }


def wsl_restart_labels() -> dict[WslRestartPolicy, str]:
    return {"auto": _("Automatically"), "ask": _("Ask first"), "never": _("Never")}


def notify_level_labels() -> dict[NotifyLevel, str]:
    return {
        "all": _("All"),
        "warnings": _("Warnings and errors"),
        "errors": _("Errors only"),
        "none": _("None"),
    }


def hotkey_check_labels() -> dict[HotkeyCheck, str]:
    return {
        "ok": _("Available"),
        "in_use": _("Used by another app"),
        "duplicate": _("Used by another action"),
        "invalid": _("Not allowed"),
        "engine_owned": _("Set by the engine"),
    }


def hotkey_rule() -> str:
    return _("Use Ctrl, Alt, Shift or Win with a key, or a function key alone. F12 is reserved by Windows.")
