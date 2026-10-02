"""The `dictation_language` setting -> the language flags of the engine the companion starts.

The engine derives everything from two flags (`app/cli/config_builder.py`):
`--transcription-language` pins what Whisper hears (and, with it, the TTS voice language),
`--output-lang` is the BCP-47 code injected into Claude's system prompt, so Claude answers
in that language. Left alone they default to Portuguese, which is why the companion always
passes both. No Qt, no I/O: the supervisor backends and the controller share it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.companion.contract import CompanionSettings, DictationLanguage, UiLanguage
from app.i18n import catalog_for

# Interface languages (the companion's own catalogs) -> the engine's two codes.
_UI_TRANSCRIPTION: Final[dict[str, str]] = {"pt-BR": "pt", "en": "en", "es": "es", "ru": "ru", "zh-CN": "zh"}
_UI_OUTPUT: Final[dict[str, str]] = {"pt-BR": "pt-BR", "en": "en", "es": "es", "ru": "ru", "zh-CN": "zh-CN"}
# Catalog names (`app.i18n.catalog_for`) -> interface language, for UI language "auto".
_CATALOG_UI: Final[dict[str, str]] = {"pt_BR": "pt-BR", "en": "en", "es": "es", "ru": "ru", "zh_CN": "zh-CN"}
# A pinned transcription language -> the BCP-47 code for Claude's answers.
_PINNED_OUTPUT: Final[dict[str, str]] = {"pt": "pt-BR", "zh": "zh-CN"}


@dataclass(frozen=True)
class EngineLanguage:
    """What goes on the engine command line (both values come from closed sets)."""

    transcription: str  # a `TranscriptionLanguage` of the engine: "auto" or an ISO 639-1 code
    output: str  # BCP-47, e.g. "pt-BR", "zh-CN"

    def cli_args(self) -> str:
        return f"--transcription-language {self.transcription} --output-lang {self.output}"


def interface_language(language: UiLanguage, catalog: str) -> str:
    """The UI language: the setting, or for "auto" the language of the catalog the OS language
    selects ("en" when it has none)."""
    if language == "auto":
        return _CATALOG_UI.get(catalog, "en")
    return language


def resolve_engine_language(dictation: DictationLanguage, language: UiLanguage, catalog: str) -> EngineLanguage:
    """The `interface` option follows the UI language; `auto` lets Whisper detect each utterance
    (Claude keeps answering in the UI language); anything else pins that language for both."""
    ui = interface_language(language, catalog)
    if dictation == "interface":
        return EngineLanguage(_UI_TRANSCRIPTION[ui], _UI_OUTPUT[ui])
    if dictation == "auto":
        return EngineLanguage("auto", _UI_OUTPUT[ui])
    return EngineLanguage(dictation, _PINNED_OUTPUT.get(dictation, dictation))


def engine_language(settings: CompanionSettings) -> EngineLanguage:
    """The flags for these settings. For a UI language on "auto" the catalog comes from
    `catalog_for` (what `set_language` will load for the setting), never from the catalog
    already loaded: the controller builds its backend before main.py calls `set_language`."""
    return resolve_engine_language(settings.dictation_language, settings.language, catalog_for(settings.language))
