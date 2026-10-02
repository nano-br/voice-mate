"""Which catalog a `language` setting selects, to tell whether changing it needs a restart.

The companion picks its catalog once at startup (`app.i18n.set_language`); the settings
window compares the selected language with the running one. "auto" and an explicit
choice can select the same catalog (Windows in English, "auto" -> "English"): then
nothing would change and no restart is needed.
"""

from __future__ import annotations

import gettext
from pathlib import Path

from app.companion.contract import UiLanguage
from app.i18n import (
    _DOMAIN,
    _LOCALES_DIR,
    _OS_LANGUAGE_CATALOGS,
    _UI_LANGUAGE_CATALOGS,
    _os_ui_language,
)


def ui_catalog(language: UiLanguage) -> str:
    """The catalog `set_language(language)` loads ("pt_BR", "es", "en"), the same way it
    does: "auto" follows the OS UI language, and a missing catalog means English."""
    if language == "auto":
        os_language = _os_ui_language() or ""
        catalog = _OS_LANGUAGE_CATALOGS.get(os_language.split("_")[0].lower(), "en")
    else:
        catalog = _UI_LANGUAGE_CATALOGS.get(language, "en")
    found = gettext.find(_DOMAIN, localedir=str(_LOCALES_DIR), languages=[catalog])
    return Path(found).parents[1].name if found else "en"
