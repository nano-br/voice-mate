from __future__ import annotations

from collections.abc import Iterator

import pytest

import app.i18n as i18n_module
from app.i18n import _, setup_locale


@pytest.fixture(autouse=True)
def reset_locale_after_test() -> Iterator[None]:
    """Restore the module-level translator after each test to avoid leaking state."""
    original = i18n_module._translation
    yield
    i18n_module._translation = original


def test_setup_locale_pt_br_translates_known_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("pt-BR")
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Chamando Claude..."


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEMATE_LANG", "pt-BR")
    setup_locale("en")
    # Mesmo passando "en" como default, env var "pt-BR" deve prevalecer.
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Chamando Claude..."


def test_missing_translation_falls_back_to_msgid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("pt-BR")
    # Uma string que não está no catálogo retorna ela mesma (msgid).
    assert _("a string that is not in the catalog") == "a string that is not in the catalog"


def test_setup_locale_with_unknown_lang_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    # Idioma sem catálogo cai no NullTranslations sem levantar.
    setup_locale("xx-YY")
    assert _("anything") == "anything"
