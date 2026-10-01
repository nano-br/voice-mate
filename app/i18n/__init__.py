"""Lightweight gettext wrapper.

The catalog is built from strings marked with `_()` across the codebase
(see `babel.cfg` + `make i18n-extract`). `setup_locale()` is called once
in `main()` after CLI parsing; runtime calls to `_()` then read from the
chosen language. Falls back to the source string when no translation is
loaded — so the app keeps working even if no `.mo` is present.
"""

from __future__ import annotations

import gettext as _gettext
import os
from pathlib import Path

_LOCALES_DIR = Path(__file__).parent / "locales"
_DOMAIN = "voicemate"
_translation: _gettext.NullTranslations = _gettext.NullTranslations()
# Catalog actually in use ("pt_BR", "es", "en"); English when none loaded (msgids are English).
_active_language = "en"


def setup_locale(default_lang: str = "pt-BR") -> None:
    """Wire up gettext. Honours the VOICEMATE_LANG env var, falls back to default.

    The default language is also used as the second-choice fallback (so an
    English user without an `en` catalog still gets the PT-BR text instead
    of an exception).
    """
    global _translation, _active_language
    lang = os.environ.get("VOICEMATE_LANG", default_lang)
    languages = [lang.replace("-", "_"), default_lang.replace("-", "_")]
    # Load ONLY the first catalog found. gettext.translation() would chain every
    # match, and since `en` keeps empty msgstrs (the msgid is the text), an
    # English request with a pt-BR default fell through to Portuguese.
    found = _gettext.find(_DOMAIN, localedir=str(_LOCALES_DIR), languages=languages)
    if found is None:
        _translation = _gettext.NullTranslations()
        _active_language = "en"
        return
    with open(found, "rb") as mo_file:
        _translation = _gettext.GNUTranslations(mo_file)
    # <locales>/<language>/LC_MESSAGES/voicemate.mo -> <language>
    _active_language = Path(found).parents[1].name


def active_language() -> str:
    """The catalog in use after `setup_locale` (e.g. "pt_BR", "es", "en").

    Published in the daemon's `/health` so the Windows script can speak the same
    language as the daemon messages it relays.
    """
    return _active_language


def _(message: str) -> str:
    """Look up the translation of `message` (returns the original if absent)."""
    return _translation.gettext(message)
