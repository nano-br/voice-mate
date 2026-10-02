"""Lightweight gettext wrapper.

The catalog is built from strings marked with `_()` across the codebase
(see `babel.cfg` + `make i18n-extract`). `setup_locale()` is called once
in `main()` after CLI parsing; runtime calls to `_()` then read from the
chosen language. Falls back to the source string when no translation is
loaded — so the app keeps working even if no `.mo` is present.

The companion (tray app) picks its language with `set_language()` instead:
from its own setting, or from the OS UI language, never from VOICEMATE_LANG.
"""

from __future__ import annotations

import gettext as _gettext
import locale
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.companion.contract import UiLanguage

_LOCALES_DIR = Path(__file__).parent / "locales"
_DOMAIN = "voicemate"
_translation: _gettext.NullTranslations = _gettext.NullTranslations()
# Catalog actually in use ("pt_BR", "es", "ru", "zh_CN", "en"); English when none loaded
# (msgids are English).
_active_language = "en"

# Companion UI languages (`UiLanguage`) -> catalog names.
_UI_LANGUAGE_CATALOGS: dict[str, str] = {"pt-BR": "pt_BR", "en": "en", "es": "es", "ru": "ru", "zh-CN": "zh_CN"}
# Language prefix -> catalog, for `set_language("auto")` and VOICEMATE_LANG: any Portuguese
# reads pt_BR better than English, any Spanish reads `es`, any Chinese (zh_TW and zh_HK
# included) reads Simplified Chinese better than English; everything else gets English.
_OS_LANGUAGE_CATALOGS: dict[str, str] = {"pt": "pt_BR", "es": "es", "en": "en", "ru": "ru", "zh": "zh_CN"}


def _load_first(languages: list[str]) -> None:
    """Load ONLY the first catalog found for `languages` (English msgids when none).

    gettext.translation() would chain every match, and since `en` keeps empty
    msgstrs (the msgid is the text), an English request with a pt-BR fallback
    fell through to Portuguese.
    """
    global _translation, _active_language
    found = _gettext.find(_DOMAIN, localedir=str(_LOCALES_DIR), languages=languages)
    if found is None:
        _translation = _gettext.NullTranslations()
        _active_language = "en"
        return
    with open(found, "rb") as mo_file:
        _translation = _gettext.GNUTranslations(mo_file)
    # <locales>/<language>/LC_MESSAGES/voicemate.mo -> <language>
    _active_language = Path(found).parents[1].name


def setup_locale(default_lang: str = "pt-BR") -> None:
    """Wire up gettext. Honours the VOICEMATE_LANG env var, falls back to default.

    The default language is also used as the second-choice fallback (so an
    English user without an `en` catalog still gets the PT-BR text instead
    of an exception).
    """
    lang = os.environ.get("VOICEMATE_LANG", default_lang)
    _load_first([_catalog_name(lang), _catalog_name(default_lang)])


def _catalog_name(lang: str) -> str:
    """A BCP-47 or POSIX language name -> the catalog to try first.

    A known language prefix picks its catalog ("pt-PT" -> pt_BR, "zh-TW" -> zh_CN,
    "ru_RU.UTF-8" -> ru); anything else keeps its own name ("fr-FR" -> fr_FR), which
    finds no catalog and falls through to the next candidate.
    """
    name = _locale_name(lang).replace("-", "_")
    return _OS_LANGUAGE_CATALOGS.get(name.split("_")[0].lower(), name)


def set_language(lang: UiLanguage) -> None:
    """Pick the companion's catalog: `lang`, or for "auto" the OS UI language.

    Unlike `setup_locale`, VOICEMATE_LANG is ignored (it configures the engine, and
    the companion has its own `language` setting). A language without a catalog
    gets English, the msgids. Called once at startup: the UI never flips mid session.
    """
    _load_first([catalog_for(lang)])


def catalog_for(lang: UiLanguage) -> str:
    """The catalog `set_language(lang)` loads ("pt_BR", "es", "ru", "zh_CN", "en"), without loading it.

    "auto" follows the OS UI language; a language without a compiled catalog means
    English. The companion compares it with `active_language()` to tell whether a new
    `language` setting needs a restart ("auto" and an explicit choice can select the
    same catalog).
    """
    if lang == "auto":
        os_language = _os_ui_language() or ""
        catalog = _OS_LANGUAGE_CATALOGS.get(os_language.split("_")[0].lower(), "en")
    else:
        catalog = _UI_LANGUAGE_CATALOGS.get(lang, "en")
    found = _gettext.find(_DOMAIN, localedir=str(_LOCALES_DIR), languages=[catalog])
    return Path(found).parents[1].name if found else "en"


def _os_ui_language() -> str | None:
    """The OS UI language as a POSIX-style name ("pt_BR", "es_MX", "zh_TW"), or None if unknown:
    the Windows display language, elsewhere `_posix_ui_language(os.environ)`."""
    if sys.platform == "win32":
        import ctypes

        # Display language of the Windows UI (not the regional format).
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return locale.windows_locale.get(langid)
    return _posix_ui_language(os.environ)


def _posix_ui_language(environ: Mapping[str, str]) -> str | None:
    """The UI language from POSIX locale variables, with GNU gettext semantics.

    The messages locale is the first one set among LC_ALL, LC_MESSAGES and LANG
    ("pt_BR.UTF-8"). When it is "C"/"POSIX" (also "C.UTF-8") or unset, the UI is
    untranslated (English, None) and LANGUAGE is ignored, like gettext does. Otherwise
    the first entry of LANGUAGE (a priority list, "pt_BR:en") wins over it.
    """
    messages_locale = ""
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        messages_locale = _locale_name(environ.get(variable, ""))
        if messages_locale:
            break
    if not messages_locale or messages_locale in ("C", "POSIX"):
        return None
    return _locale_name(environ.get("LANGUAGE", "").split(":")[0]) or messages_locale


def _locale_name(value: str) -> str:
    """ "pt_BR.UTF-8@euro" -> "pt_BR"."""
    return value.split(".")[0].split("@")[0].strip()


def active_language() -> str:
    """The catalog in use after `setup_locale` (e.g. "pt_BR", "es", "ru", "zh_CN", "en").

    Published in the daemon's `/health` so the Windows script can speak the same
    language as the daemon messages it relays.
    """
    return _active_language


def _(message: str) -> str:
    """Look up the translation of `message` (returns the original if absent)."""
    return _translation.gettext(message)
