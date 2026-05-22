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


def setup_locale(default_lang: str = "pt-BR") -> None:
    """Wire up gettext. Honours the VOICEMATE_LANG env var, falls back to default.

    The default language is also used as the second-choice fallback (so an
    English user without an `en` catalog still gets the PT-BR text instead
    of an exception).
    """
    global _translation
    lang = os.environ.get("VOICEMATE_LANG", default_lang)
    normalized = lang.replace("-", "_")
    fallback = default_lang.replace("-", "_")
    try:
        _translation = _gettext.translation(
            _DOMAIN,
            localedir=str(_LOCALES_DIR),
            languages=[normalized, fallback],
        )
    except FileNotFoundError:
        _translation = _gettext.NullTranslations()


def _(message: str) -> str:
    """Look up the translation of `message` (returns the original if absent)."""
    return _translation.gettext(message)
