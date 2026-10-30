"""The VoiceMate settings the assistant may change by voice, and how a change is checked.

`catalog` is the `settings` list of `assistant.json`: for each setting its dotted key, type
(choice, bool, number or text), allowed values or range, current value and a one-line English
description the skill reads. `change` turns a `set_setting` action into new settings, or a
`Refusal` with a code: only keys of the catalog, only valid values, nothing is ever guessed.
Secrets, hotkeys, files and folders (the personality file and text, the notes vault, the custom
skill folder, the Desktop profile override) and the engine (folder, WSL distro, port, mode) are
left out on purpose: they are not safe to change by voice. The permission mode is in, but only
default and acceptEdits: bypassPermissions is only chosen in Settings. Standard library only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final, Literal, get_args

from app.companion import choice_labels
from app.companion.contract import (
    AnswerDelivery,
    AssistantEffort,
    AssistantModel,
    AssistantPermission,
    AssistantVoiceSource,
    CompanionSettings,
    CueSettings,
    DictationLanguage,
    ExpressionsMode,
    NotifyLevel,
    PersonalitySource,
    SkillLanguage,
)
from app.i18n import _
from app.protocol.models import SttMode

SettingType = Literal["choice", "bool", "number", "text"]
RefusalCode = Literal[
    "unknown_key",
    "not_allowed",
    "out_of_range",
    "not_bool",
    "bad_text",
    "no_desktop_conversation",
    "no_personality_file",
]
TEXT_LIMIT: Final = 200
# By voice the permission can only stay or go down: full access (bypassPermissions) is a choice made
# in Settings, where the warning is, never by an action a quoted text could have suggested.
VOICE_PERMISSIONS: Final[tuple[AssistantPermission, ...]] = ("default", "acceptEdits")

Value = str | bool | float


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: SettingType
    description: str  # English, for the skill
    label: Callable[[], str]  # localized, for the notice
    get: Callable[[CompanionSettings], Value]
    put: Callable[[CompanionSettings, Value], CompanionSettings]
    values: tuple[str, ...] = ()
    range: tuple[int, int] | None = None


@dataclass(frozen=True)
class Refusal:
    code: RefusalCode
    detail: str = ""  # the allowed values or the range, for the messages


def _assistant(name: str) -> Callable[[CompanionSettings, Value], CompanionSettings]:
    def put(settings: CompanionSettings, value: Value) -> CompanionSettings:
        return replace(settings, assistant=replace(settings.assistant, **{name: value}))  # type: ignore[arg-type]

    return put


def _top(name: str) -> Callable[[CompanionSettings, Value], CompanionSettings]:
    def put(settings: CompanionSettings, value: Value) -> CompanionSettings:
        return replace(settings, **{name: value})  # type: ignore[arg-type]

    return put


def _volume(settings: CompanionSettings, value: Value) -> CompanionSettings:
    return replace(settings, master_volume=float(value) / 100)


def _answer_cue(settings: CompanionSettings) -> CueSettings:
    return settings.cues.get("answer", CueSettings())


def _put_answer_cue(name: str) -> Callable[[CompanionSettings, Value], CompanionSettings]:
    def put(settings: CompanionSettings, value: Value) -> CompanionSettings:
        new = value / 100 if name == "volume" and not isinstance(value, bool | str) else value
        cues = dict(settings.cues)
        cues["answer"] = replace(_answer_cue(settings), **{name: new})  # type: ignore[arg-type]
        return replace(settings, cues=cues)

    return put


SPECS: Final[tuple[SettingSpec, ...]] = (
    SettingSpec(
        "assistant.mode",
        "choice",
        "Where the central conversation runs: VoiceMate's own conversation or a Claude Desktop conversation.",
        lambda: _("Assistant mode"),
        lambda s: s.assistant.mode,
        _assistant("mode"),
        values=("own_session", "desktop"),
    ),
    SettingSpec(
        "assistant.model",
        "choice",
        "The model of the central conversation. In own_session it applies from the next turn; in desktop "
        "VoiceMate cannot change it and tells you to apply it with set_session_model.",
        lambda: _("Assistant model"),
        lambda s: s.assistant.model,
        _assistant("model"),
        values=get_args(AssistantModel),
    ),
    SettingSpec(
        "assistant.effort",
        "choice",
        "How hard the central conversation thinks. In own_session changing it restarts the conversation; "
        "in desktop VoiceMate tells you to apply it with set_session_effort.",
        lambda: _("Assistant effort"),
        lambda s: s.assistant.effort,
        _assistant("effort"),
        values=get_args(AssistantEffort),
    ),
    SettingSpec(
        "assistant.permission_mode",
        "choice",
        "Permission mode of every conversation VoiceMate starts (the central one included; a change "
        "restarts it). bypassPermissions can only be chosen in Settings.",
        lambda: _("Permissions"),
        lambda s: s.assistant.permission_mode,
        _assistant("permission_mode"),
        values=VOICE_PERMISSIONS,
    ),
    SettingSpec(
        "assistant.answer_delivery",
        "choice",
        "How answers of other conversations reach the person: speak (you tell them at once), announce "
        "(a short notice and a sound, the answer waits in the inbox), sound (only a sound), silent (inbox only).",
        lambda: _("Answers from other conversations"),
        lambda s: s.assistant.answer_delivery,
        _assistant("answer_delivery"),
        values=get_args(AnswerDelivery),
    ),
    SettingSpec(
        "assistant.new_conversation_model",
        "choice",
        "The model start_conversation uses when you pass none.",
        lambda: _("Model of new conversations"),
        lambda s: s.assistant.new_conversation_model,
        _assistant("new_conversation_model"),
        values=get_args(AssistantModel),
    ),
    SettingSpec(
        "assistant.new_conversation_effort",
        "choice",
        "The effort start_conversation uses when you pass none.",
        lambda: _("Effort of new conversations"),
        lambda s: s.assistant.new_conversation_effort,
        _assistant("new_conversation_effort"),
        values=get_args(AssistantEffort),
    ),
    SettingSpec(
        "assistant.desktop_conversation",
        "text",
        "Exact title of the Claude Desktop conversation used as the central one in the desktop mode.",
        lambda: _("Desktop conversation"),
        lambda s: s.assistant.desktop_conversation,
        _assistant("desktop_conversation"),
    ),
    SettingSpec(
        "assistant.speak_replies",
        "bool",
        "Whether VoiceMate speaks your voice block with the engine's voice (off: the replies are only shown).",
        lambda: _("Speak the replies"),
        lambda s: s.assistant.speak_replies,
        _assistant("speak_replies"),
    ),
    SettingSpec(
        "assistant.voice_source",
        "choice",
        "Who speaks the replies: auto (the engine's voice, Windows' own voices while the engine cannot speak), "
        "engine (only the engine) or windows (only Windows' voices, no setup needed).",
        lambda: _("Voice source"),
        lambda s: s.assistant.voice_source,
        _assistant("voice_source"),
        values=get_args(AssistantVoiceSource),
    ),
    SettingSpec(
        "assistant.voice",
        "text",
        "The voice id VoiceMate speaks with (empty: the default voice of the source; Windows' voices start "
        "with windows/). Only ids the person picked or confirmed; an unknown id leaves the replies unspoken.",
        lambda: _("Assistant voice"),
        lambda s: s.assistant.voice,
        _assistant("voice"),
    ),
    SettingSpec(
        "assistant.voice_commands",
        "bool",
        "Whether VoiceMate itself understands voice commands (switch conversation, talk directly...).",
        lambda: _("Voice commands"),
        lambda s: s.assistant.voice_commands,
        _assistant("voice_commands"),
    ),
    SettingSpec(
        "assistant.expressions",
        "choice",
        "Whether the voice block may carry expression tags.",
        lambda: _("Expression tags"),
        lambda s: s.assistant.expressions,
        _assistant("expressions"),
        values=get_args(ExpressionsMode),
    ),
    SettingSpec(
        "assistant.user_name",
        "text",
        "How the assistant addresses the person (empty: no name).",
        lambda: _("Your name"),
        lambda s: s.assistant.user_name,
        _assistant("user_name"),
    ),
    SettingSpec(
        "assistant.personality_source",
        "choice",
        "Where your personality comes from: none, the person's Markdown file (only when one is chosen in "
        "Settings) or the text the person wrote in Settings.",
        lambda: _("Personality"),
        lambda s: s.assistant.personality_source,
        _assistant("personality_source"),
        values=get_args(PersonalitySource),
    ),
    SettingSpec(
        "assistant.desktop_profile_auto_update",
        "bool",
        "Whether VoiceMate checks for a newer Claude Desktop profile (how it finds the Desktop's controls) at start.",
        lambda: _("Update the Claude Desktop profile automatically"),
        lambda s: s.assistant.desktop_profile_auto_update,
        _assistant("desktop_profile_auto_update"),
    ),
    SettingSpec(
        "assistant.skill_language",
        "choice",
        "Language of the assistant skill: auto follows the interface (pt-BR skill for pt-BR, English otherwise).",
        lambda: _("Skill language"),
        lambda s: s.assistant.skill_language,
        _assistant("skill_language"),
        values=get_args(SkillLanguage),
    ),
    SettingSpec(
        "cues.enabled",
        "bool",
        "Whether VoiceMate plays its sounds.",
        lambda: _("Sounds"),
        lambda s: s.cues_enabled,
        _top("cues_enabled"),
    ),
    SettingSpec(
        "cues.volume",
        "number",
        "Volume of VoiceMate's sounds, in percent.",
        lambda: _("Sound volume"),
        lambda s: float(round(s.master_volume * 100)),
        _volume,
        range=(0, 100),
    ),
    SettingSpec(
        "cues.answer.enabled",
        "bool",
        "Whether the answer sound plays when another conversation's answer is ready.",
        lambda: _("Answer ready"),
        lambda s: _answer_cue(s).enabled,
        _put_answer_cue("enabled"),
    ),
    SettingSpec(
        "cues.answer.volume",
        "number",
        "Volume of the answer sound, in percent (on top of the sound volume).",
        lambda: _("Answer sound volume"),
        lambda s: float(round(_answer_cue(s).volume * 100)),
        _put_answer_cue("volume"),
        range=(0, 100),
    ),
    SettingSpec(
        "general.dictation_language",
        "choice",
        "Language of the dictation: interface follows VoiceMate's language, auto detects each utterance.",
        lambda: _("Dictation language"),
        lambda s: s.dictation_language,
        _top("dictation_language"),
        values=get_args(DictationLanguage),
    ),
    SettingSpec(
        "general.stt_mode",
        "choice",
        "Where the transcription runs (changing it restarts the engine for a moment).",
        lambda: _("Transcription"),
        lambda s: s.stt_mode,
        _top("stt_mode"),
        values=get_args(SttMode),
    ),
    SettingSpec(
        "general.notify_level",
        "choice",
        "Which notifications VoiceMate shows.",
        lambda: _("Notifications"),
        lambda s: s.notify_level,
        _top("notify_level"),
        values=get_args(NotifyLevel),
    ),
)
_BY_KEY: Final[dict[str, SettingSpec]] = {spec.key: spec for spec in SPECS}


def spec(key: str) -> SettingSpec | None:
    return _BY_KEY.get(key)


def _json_value(spec: SettingSpec, value: Value) -> object:
    if spec.type == "number" and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def catalog(settings: CompanionSettings) -> list[dict[str, object]]:
    """The `settings` list of `assistant.json`, in a stable order."""
    entries: list[dict[str, object]] = []
    for item in SPECS:
        entry: dict[str, object] = {"key": item.key, "type": item.type}
        if item.values:
            entry["values"] = list(item.values)
        if item.range is not None:
            entry["range"] = list(item.range)
        entry["value"] = _json_value(item, item.get(settings))
        entry["description"] = item.description
        entries.append(entry)
    return entries


def change(settings: CompanionSettings, key: str, value: object) -> CompanionSettings | Refusal:
    """The settings with `key` set to `value`, or why that is refused."""
    item = _BY_KEY.get(key)
    if item is None:
        return Refusal("unknown_key")
    checked: Value
    if item.type == "choice":
        if not isinstance(value, str) or value not in item.values:
            return Refusal("not_allowed", ", ".join(item.values))
        checked = value
    elif item.type == "bool":
        if not isinstance(value, bool):
            return Refusal("not_bool")
        checked = value
    elif item.type == "number":
        low, high = item.range or (0, 0)
        if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
            return Refusal("out_of_range", f"{low}-{high}")
        checked = float(value)
    else:
        if not isinstance(value, str) or len(value) > TEXT_LIMIT or "\n" in value or "\r" in value:
            return Refusal("bad_text")
        checked = value.strip()
    if key == "assistant.mode" and checked == "desktop" and not settings.assistant.desktop_conversation.strip():
        return Refusal("no_desktop_conversation")
    if key == "assistant.personality_source" and checked == "file" and not settings.assistant.personality_file:
        return Refusal("no_personality_file")
    return item.put(settings, checked)


def display_value(key: str, value: object) -> str:
    """The value as the notice shows it (localized on/off for switches)."""
    item = _BY_KEY.get(key)
    if isinstance(value, bool):
        return _("on") if value else _("off")
    if item is not None and item.type == "number" and isinstance(value, int | float):
        return f"{value:g}"
    if item is not None and item.type == "choice":
        labels = _choice_labels().get(key)
        if labels is not None:
            return labels().get(value, str(value))
    return str(value)


def _choice_labels() -> dict[str, Callable[[], Mapping[Any, str]]]:
    """The settings window's names for each choice."""
    return {
        "assistant.mode": choice_labels.assistant_mode_labels,
        "assistant.model": choice_labels.assistant_model_labels,
        "assistant.effort": choice_labels.assistant_effort_labels,
        "assistant.permission_mode": choice_labels.assistant_permission_labels,
        "assistant.answer_delivery": choice_labels.answer_delivery_labels,
        "assistant.voice_source": choice_labels.voice_source_labels,
        "assistant.new_conversation_model": choice_labels.assistant_model_labels,
        "assistant.new_conversation_effort": choice_labels.assistant_effort_labels,
        "assistant.expressions": choice_labels.expressions_labels,
        "assistant.personality_source": choice_labels.personality_source_labels,
        "assistant.skill_language": choice_labels.skill_language_labels,
        "general.dictation_language": choice_labels.dictation_language_labels,
        "general.stt_mode": choice_labels.stt_mode_labels,
        "general.notify_level": choice_labels.notify_level_labels,
    }


def refusal_reason(refusal: Refusal) -> str:
    """Localized, for the notice."""
    if refusal.code == "unknown_key":
        return _("the assistant cannot change this setting")
    if refusal.code == "not_allowed":
        return _("the value must be one of: {values}").format(values=refusal.detail)
    if refusal.code == "out_of_range":
        return _("the value must be in the range {range}").format(range=refusal.detail)
    if refusal.code == "not_bool":
        return _("the value must be on or off")
    if refusal.code == "bad_text":
        return _("the value must be a short single line of text")
    if refusal.code == "no_personality_file":
        return _("choose the personality file first, in Settings, Assistant")
    return _("choose the Desktop conversation first, in Settings, Assistant")


def refusal_reason_en(refusal: Refusal) -> str:
    """English, for the log and the note to the assistant."""
    return {
        "unknown_key": "it is not in the settings catalog",
        "not_allowed": f"the value must be one of: {refusal.detail}",
        "out_of_range": f"the value must be in the range {refusal.detail}",
        "not_bool": "the value must be true or false",
        "bad_text": f"the value must be a single line of at most {TEXT_LIMIT} characters",
        "no_desktop_conversation": "no Desktop conversation is chosen yet (Settings, Assistant)",
        "no_personality_file": "no personality file is chosen yet (Settings, Assistant)",
    }[refusal.code]
