"""Localized labels shared by the tray, the status window and the settings window.

Every user-facing string of the UI goes through `app.i18n._` at call time (the
language is chosen once at startup, before any widget exists). Labels for
`Literal` options live in dicts built by functions, so they are translated when
read, never at import time.
"""

from __future__ import annotations

import time

from app.companion.assistant.contract import AssistantSnapshot
from app.companion.choice_labels import (
    answer_delivery_labels as answer_delivery_labels,
)
from app.companion.choice_labels import (
    assistant_effort_labels as assistant_effort_labels,
)
from app.companion.choice_labels import (
    assistant_mode_labels as assistant_mode_labels,
)
from app.companion.choice_labels import (
    assistant_model_labels as assistant_model_labels,
)
from app.companion.choice_labels import (
    assistant_permission_labels as assistant_permission_labels,
)
from app.companion.choice_labels import (
    dictation_language_labels as dictation_language_labels,
)
from app.companion.choice_labels import (
    expressions_labels as expressions_labels,
)
from app.companion.choice_labels import (
    notify_level_labels as notify_level_labels,
)
from app.companion.choice_labels import (
    personality_source_labels as personality_source_labels,
)
from app.companion.choice_labels import (
    skill_language_labels as skill_language_labels,
)
from app.companion.choice_labels import (
    stt_mode_labels as stt_mode_labels,
)
from app.companion.contract import (
    AssistantHotkeyAction,
    CompanionSnapshot,
    CueName,
    CuePreset,
    CueSource,
    EngineMode,
    FlowEntry,
    HotkeyCheck,
    SupervisorState,
    TrayState,
    TtsChoice,
    UiLanguage,
    WslRestartPolicy,
)
from app.companion.ui.chords import display_chord
from app.i18n import _
from app.protocol.models import AudioHealth, FlowKind, SttInfo, SttModel

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
            return _("Listening for {chord}").format(chord=display_chord(idle_chord))
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


def tooltip(
    snapshot: CompanionSnapshot, idle_chord: str = "", now: float | None = None, *, answers_waiting: int = 0
) -> str:
    line = status_line(snapshot, idle_chord, now)
    if snapshot.hotkeys_suspended and snapshot.tray_state == "idle":
        line = _("Shortcuts paused")
    text = f"{APP_NAME}\n{line}"
    if answers_waiting:
        text += "\n" + _("Answers waiting: {count}").format(count=answers_waiting)
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
        "answer": _("Answer ready"),
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
        "ru": "Русский",
        "zh-CN": "中文（简体）",
    }


def engine_mode_labels() -> dict[EngineMode, str]:
    return {
        "wsl2": _("Start it in WSL"),
        "local": _("Start it on this computer"),
        "external": _("Connect only"),
    }


def wsl_restart_labels() -> dict[WslRestartPolicy, str]:
    return {"auto": _("Automatically"), "ask": _("Ask first"), "never": _("Never")}


def profile_source_label(source: str) -> str:
    return {
        "override": _("imported file"),
        "remote": _("downloaded update"),
        "packaged": _("built into VoiceMate"),
    }.get(source, source)


def assistant_hotkey_titles() -> dict[AssistantHotkeyAction, str]:
    return {
        "assistant_talk": _("Talk to the assistant"),
        "assistant_window": _("Open the assistant window"),
        "assistant_mode": _("Switch the assistant mode"),
        "assistant_back": _("Back to the assistant"),
        "assistant_digest": _("Hear what's ready"),
    }


def assistant_button_tip(window_chord: str) -> str:
    """Tooltip of the status window's Assistant button: what it does and its hotkey."""
    title = _("Open the assistant window")
    if not window_chord:
        return title
    return f"{title} ({display_chord(window_chord)})"


def assistant_state_line(snapshot: AssistantSnapshot) -> str:
    """The assistant window's status text (the session link is added by the window)."""
    if snapshot.state == "error":
        return {
            "cli_missing": _("Claude Code was not found"),
            "not_logged_in": _("Claude Code is not signed in"),
            "start_failed": _("The assistant could not start"),
            "exited": _("The assistant stopped"),
            "setup_failed": _("The assistant could not be set up"),
            "no_conversation": _("No Claude Desktop conversation is chosen"),
        }.get(snapshot.error or "", _("The assistant has a problem"))
    return {
        "off": _("The assistant is off"),
        "starting": _("Starting..."),
        "ready": _("Ready"),
        "busy": _("Answering..."),
        "stopped": _("Stopped"),
    }[snapshot.state]


def assistant_input_block(snapshot: AssistantSnapshot) -> str:
    """Why the message box is disabled, or "" when it accepts text."""
    if snapshot.state == "ready":
        return ""
    if snapshot.state == "error":
        return {
            "cli_missing": _(
                "Install Claude Code, or set VOICEMATE_CLAUDE_CLI to its path, then restart the assistant."
            ),
            "not_logged_in": _('Sign in with "claude auth login" in a terminal, then restart the assistant.'),
            "no_conversation": _("Choose the Claude Desktop conversation in Settings, Assistant."),
        }.get(snapshot.error or "", _("Restart the assistant to try again. The VoiceMate log has the details."))
    return {
        "off": _("The assistant is off. Turn it on in Settings, Assistant."),
        "starting": _("The assistant is starting. Please wait."),
        "busy": _("The assistant is answering. Please wait."),
        "stopped": _("The assistant is stopped."),
    }[snapshot.state]


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


def stt_model_labels() -> dict[SttModel, str]:
    # Model names are Whisper's own: never translated, only described.
    return {
        "large-v3-turbo": _("large-v3-turbo (recommended)"),
        "large-v3": _("large-v3 (most accurate, slowest)"),
        "medium": _("medium (faster)"),
        "small": _("small (fastest, less accurate)"),
    }


def tts_engine_labels() -> dict[TtsChoice, str]:
    return {
        "default": _("Built-in (engine setup)"),
        "external": _("Custom provider (HTTP)"),
    }


def stt_line(stt: SttInfo | None) -> str:
    """Where the engine is transcribing, for the status window."""
    if stt is None:
        return _("Not known yet")
    device = _("GPU") if stt["device"] == "gpu" else _("CPU")
    line = _("{device}, {model}").format(device=device, model=stt["model"])
    if stt["mode"] == "gpu" and stt["device"] == "cpu":
        line += "\n" + _("The GPU mode is not working. Install it from the Transcription menu.")
    return line


def download_size(megabytes: int) -> str:
    if megabytes >= 1000:
        return _("about {size} GB").format(size=f"{megabytes / 1000:.1f}")
    return _("about {size} MB").format(size=max(megabytes, 1))
