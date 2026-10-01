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
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.companion.contract import UiLanguage

_LOCALES_DIR = Path(__file__).parent / "locales"
_DOMAIN = "voicemate"
_translation: _gettext.NullTranslations = _gettext.NullTranslations()
# Catalog actually in use ("pt_BR", "es", "en"); English when none loaded (msgids are English).
_active_language = "en"

# Companion UI languages (`UiLanguage`) -> catalog names.
_UI_LANGUAGE_CATALOGS: dict[str, str] = {"pt-BR": "pt_BR", "en": "en", "es": "es"}
# OS language prefix -> catalog, for `set_language("auto")`: any Portuguese reads pt_BR
# better than English, any Spanish reads `es`; everything else gets English.
_OS_LANGUAGE_CATALOGS: dict[str, str] = {"pt": "pt_BR", "es": "es", "en": "en"}


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
    _load_first([lang.replace("-", "_"), default_lang.replace("-", "_")])


def set_language(lang: UiLanguage) -> None:
    """Pick the companion's catalog: `lang`, or for "auto" the OS UI language.

    Unlike `setup_locale`, VOICEMATE_LANG is ignored (it configures the engine, and
    the companion has its own `language` setting). A language without a catalog
    gets English, the msgids. Called once at startup: the UI never flips mid session.
    """
    if lang == "auto":
        os_language = _os_ui_language() or ""
        catalog = _OS_LANGUAGE_CATALOGS.get(os_language.split("_")[0].lower(), "en")
    else:
        catalog = _UI_LANGUAGE_CATALOGS.get(lang, "en")
    _load_first([catalog])


def _os_ui_language() -> str | None:
    """The OS UI language as a POSIX-style name ("pt_BR", "es_MX"), or None if unknown.

    POSIX: the first variable set among LANGUAGE, LC_ALL, LC_MESSAGES and LANG decides.
    LANGUAGE is a priority list ("pt_BR:en"); the others hold one locale ("pt_BR.UTF-8").
    The "C"/"POSIX" locale (also "C.UTF-8") means untranslated, so English (None).
    """
    if sys.platform == "win32":
        import ctypes

        # Display language of the Windows UI (not the regional format).
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return locale.windows_locale.get(langid)
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable, "").split(":")[0].split(".")[0].split("@")[0]
        if value:
            return None if value in ("C", "POSIX") else value
    return None


def active_language() -> str:
    """The catalog in use after `setup_locale` (e.g. "pt_BR", "es", "en").

    Published in the daemon's `/health` so the Windows script can speak the same
    language as the daemon messages it relays.
    """
    return _active_language


def _(message: str) -> str:
    """Look up the translation of `message` (returns the original if absent)."""
    return _translation.gettext(message)
