"""Localized names of the settings that are a choice among a few values.

Qt-free, so the assistant core can show the same names as the settings window
(app/companion/ui/texts.py re-exports them). Read at call time, never at import time.
"""

from __future__ import annotations

from app.companion.contract import (
    AnswerDelivery,
    AssistantEffort,
    AssistantMode,
    AssistantModel,
    AssistantPermission,
    AssistantVoiceSource,
    DictationLanguage,
    ExpressionsMode,
    NotifyLevel,
    PersonalitySource,
    SkillLanguage,
)
from app.i18n import _
from app.protocol.models import SttMode


def dictation_language_labels() -> dict[DictationLanguage, str]:
    # Same convention as the interface languages: each one in its own language.
    return {
        "interface": _("Same as the interface"),
        "auto": _("Detect automatically"),
        "pt": "Português",
        "en": "English",
        "es": "Español",
        "ru": "Русский",
        "zh": "中文",
        "fr": "Français",
        "de": "Deutsch",
        "it": "Italiano",
        "ja": "日本語",
    }


def assistant_mode_labels() -> dict[AssistantMode, str]:
    return {
        "off": _("Off"),
        "own_session": _("VoiceMate's own conversation"),
        "desktop": _("A Claude Desktop conversation"),
    }


def assistant_permission_labels() -> dict[AssistantPermission, str]:
    return {
        "default": _("Ask before every change"),
        "acceptEdits": _("Accept file edits, ask for the rest"),
        "bypassPermissions": _("Full access, never ask (risky)"),
    }


def skill_language_labels() -> dict[SkillLanguage, str]:
    return {"auto": _("Same as VoiceMate"), "en": _("English"), "pt-BR": _("Portuguese (Brazil)")}


def answer_delivery_labels() -> dict[AnswerDelivery, str]:
    return {
        "speak": _("Tell me the answer as soon as it arrives"),
        "announce": _("Say a short notice and keep the answer for later"),
        "sound": _("Only play a sound and keep the answer"),
        "silent": _("Just keep it"),
    }


def voice_source_labels() -> dict[AssistantVoiceSource, str]:
    return {
        "auto": _("Automatic: the engine's voice, or the voices of Windows when the engine has none"),
        "engine": _("The engine's voice"),
        "windows": _("The voices of Windows"),
    }


def personality_source_labels() -> dict[PersonalitySource, str]:
    return {
        "none": _("None"),
        "file": _("A Markdown file (for example a note in your Obsidian vault)"),
        "custom": _("Write my own"),
    }


def assistant_model_labels() -> dict[AssistantModel, str]:
    return {"haiku": _("Haiku (fastest)"), "sonnet": _("Sonnet"), "opus": _("Opus (deepest)")}


def assistant_effort_labels() -> dict[AssistantEffort, str]:
    return {"low": _("Low"), "medium": _("Medium"), "high": _("High")}


def expressions_labels() -> dict[ExpressionsMode, str]:
    return {"off": _("Off"), "on": _("On")}


def notify_level_labels() -> dict[NotifyLevel, str]:
    return {
        "all": _("All"),
        "warnings": _("Warnings and errors"),
        "errors": _("Errors only"),
        "none": _("None"),
    }


def stt_mode_labels() -> dict[SttMode, str]:
    return {
        "cpu": _("CPU (leaves the GPU free)"),
        "gpu": _("GPU (fastest)"),
        "auto": _("Automatic (engine setup)"),
    }
