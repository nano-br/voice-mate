from __future__ import annotations

import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Literal

import pytest
from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.frontend import parse_mapping_cfg
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

import app.i18n as i18n_module
from app.i18n import _, active_language, set_language, setup_locale

_SOURCE_LOCALES_DIR = Path(i18n_module.__file__).parent / "locales"
_DOMAIN = "voicemate"

# Every user-facing string must exist in these three catalogs.
ALL_LOCALES = ("en", "pt_BR", "es")
# `en` msgstrs are intentionally empty (the English msgid is the source text);
# these locales must translate every msgid.
TRANSLATED_LOCALES = ("pt_BR", "es")


def _symbols(text: str) -> list[str]:
    """Emojis and pictographic symbols (⚠ ✓ ✗ ❌ 🎙 ☕ ↳ ...): Unicode category "So"."""
    return [char for char in text if unicodedata.category(char) == "So"]


# Tokens that must survive translation unchanged (compared as multisets, so the
# translation is free to reorder them).
_PRESERVED_TOKENS: dict[str, Callable[[str], list[str]]] = {
    "brace placeholder": re.compile(r"\{[^{}]*\}").findall,
    "printf placeholder": re.compile(r"%(?:\([A-Za-z_]\w*\))?[#0+-]?\d*(?:\.\d+)?[sdifrx]").findall,
    "backtick-quoted command": re.compile(r"`[^`]+`").findall,
    "URL": re.compile(r"https?://[^\s)]*[^\s).,;:]").findall,
    "line tag": re.compile(r"\[[A-Za-z]+\]").findall,
    "emoji/symbol": _symbols,
}


def _po_path(locale: str, root: Path = _SOURCE_LOCALES_DIR) -> Path:
    return root / locale / "LC_MESSAGES" / f"{_DOMAIN}.po"


def _load_catalog(locale: str) -> Catalog:
    with _po_path(locale).open("rb") as po_file:
        return read_po(po_file, locale=locale)


def _message_pairs(locale: str) -> list[tuple[str, str]]:
    """(msgid, msgstr) for every message of the catalog (header excluded)."""
    pairs: list[tuple[str, str]] = []
    for message in _load_catalog(locale):
        if not message.id:
            continue
        # `_()` is plain gettext: plural entries would need their own checks.
        assert isinstance(message.id, str), f"unexpected plural entry: {message.id!r}"
        assert isinstance(message.string, str), f"unexpected plural msgstr for {message.id!r}"
        pairs.append((message.id, message.string))
    return pairs


def _edges(text: str) -> tuple[str, str]:
    """Leading and trailing whitespace (newlines and indentation are part of the output layout)."""
    return text[: len(text) - len(text.lstrip())], text[len(text.rstrip()) :]


@pytest.fixture(scope="session")
def compiled_locales_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile the tracked .po files into a temp tree.

    `.mo` files are gitignored, so the runtime tests must not depend on a prior
    `make i18n-compile` (nor on a stale `.mo` left over from an older catalog).
    """
    root = tmp_path_factory.mktemp("locales")
    for locale in ALL_LOCALES:
        mo_path = root / locale / "LC_MESSAGES" / f"{_DOMAIN}.mo"
        mo_path.parent.mkdir(parents=True)
        with mo_path.open("wb") as mo_file:
            write_mo(mo_file, _load_catalog(locale))
    return root


@pytest.fixture(autouse=True)
def use_compiled_catalogs(compiled_locales_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `setup_locale()` at the catalogs freshly compiled from the .po files."""
    monkeypatch.setattr(i18n_module, "_LOCALES_DIR", compiled_locales_dir)


@pytest.fixture(autouse=True)
def reset_locale_after_test() -> Iterator[None]:
    """Restore the module-level translator after each test to avoid leaking state."""
    original = i18n_module._translation
    original_language = i18n_module._active_language
    yield
    i18n_module._translation = original
    i18n_module._active_language = original_language


def test_setup_locale_pt_br_translates_known_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("pt-BR")
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Chamando Claude..."


def test_setup_locale_es_translates_known_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("es")
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Llamando a Claude..."


def test_setup_locale_regional_spanish_uses_es_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    # There is no es_MX catalog: gettext must fall back to the generic `es` one.
    setup_locale("es-MX")
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Llamando a Claude..."


def test_setup_locale_en_returns_source_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("en")
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Calling Claude..."


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEMATE_LANG", "pt-BR")
    setup_locale("en")
    # Even passing "en" as the default, the env var "pt-BR" must take precedence.
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Chamando Claude..."


def test_env_var_selects_spanish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEMATE_LANG", "es")
    setup_locale("pt-BR")
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Llamando a Claude..."


def test_missing_translation_falls_back_to_msgid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("pt-BR")
    # A string that is not in the catalog returns itself (the msgid).
    assert _("a string that is not in the catalog") == "a string that is not in the catalog"


@pytest.mark.parametrize(
    ("requested", "default", "expected"),
    [
        ("es", "pt-BR", "es"),
        ("es-MX", "pt-BR", "es"),
        ("pt-BR", "en", "pt_BR"),
        ("en", "pt-BR", "en"),
        ("fr", "pt-BR", "pt_BR"),  # no fr catalog: the default is what actually loads
        ("fr", "de", "en"),  # nothing loads: msgids (English) are shown
    ],
)
def test_active_language_reports_the_catalog_actually_loaded(
    monkeypatch: pytest.MonkeyPatch, requested: str, default: str, expected: str
) -> None:
    """Published in /health: the Windows script follows it, so it must name the catalog
    really in use (after the fallback chain), not just what was asked for."""
    monkeypatch.setenv("VOICEMATE_LANG", requested)
    setup_locale(default)
    assert active_language() == expected


def test_env_var_en_beats_a_pt_br_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """README: `VOICEMATE_LANG=en make run` must print English. With chained catalogs
    the empty `en` msgstrs fell through to pt_BR while /health claimed "en"."""
    monkeypatch.setenv("VOICEMATE_LANG", "en")
    setup_locale("pt-BR")
    assert _("[VoiceMate] 🤖 Calling Claude...") == "[VoiceMate] 🤖 Calling Claude..."
    assert active_language() == "en"


def test_reported_language_matches_the_text_actually_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    for requested, expected_lang, expected_text in [
        ("es", "es", "[VoiceMate] 🤖 Llamando a Claude..."),
        ("pt-BR", "pt_BR", "[VoiceMate] 🤖 Chamando Claude..."),
        ("en", "en", "[VoiceMate] 🤖 Calling Claude..."),
    ]:
        monkeypatch.setenv("VOICEMATE_LANG", requested)
        setup_locale("pt-BR")
        shown = (active_language(), _("[VoiceMate] 🤖 Calling Claude..."))
        assert shown == (expected_lang, expected_text), requested


@pytest.mark.parametrize(
    ("language", "expected_lang", "expected_text"),
    [
        ("pt-BR", "pt_BR", "[VoiceMate] 🤖 Chamando Claude..."),
        ("es", "es", "[VoiceMate] 🤖 Llamando a Claude..."),
        ("en", "en", "[VoiceMate] 🤖 Calling Claude..."),
    ],
)
def test_set_language_loads_the_companion_language_and_ignores_the_env_var(
    monkeypatch: pytest.MonkeyPatch, language: Literal["pt-BR", "en", "es"], expected_lang: str, expected_text: str
) -> None:
    """The companion has its own `language` setting: VOICEMATE_LANG configures the engine."""
    monkeypatch.setenv("VOICEMATE_LANG", "es" if language != "es" else "pt-BR")
    set_language(language)
    assert (active_language(), _("[VoiceMate] 🤖 Calling Claude...")) == (expected_lang, expected_text)


@pytest.mark.parametrize(
    ("os_language", "expected_lang"),
    [
        ("pt_BR", "pt_BR"),
        ("pt_PT", "pt_BR"),  # any Portuguese reads pt_BR better than English
        ("es_MX", "es"),
        ("en_US", "en"),
        ("fr_FR", "en"),  # no catalog: English msgids, never the pt-BR engine default
        (None, "en"),
    ],
)
def test_set_language_auto_follows_the_os_ui_language(
    monkeypatch: pytest.MonkeyPatch, os_language: str | None, expected_lang: str
) -> None:
    monkeypatch.setenv("VOICEMATE_LANG", "pt-BR")
    monkeypatch.setattr(i18n_module, "_os_ui_language", lambda: os_language)
    set_language("auto")
    assert active_language() == expected_lang


def test_os_ui_language_reads_the_posix_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows reads GetUserDefaultUILanguage, not the environment")
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("LANG", "es_MX.UTF-8")
    assert i18n_module._os_ui_language() == "es_MX"
    monkeypatch.setenv("LANGUAGE", "pt_BR:en")
    assert i18n_module._os_ui_language() == "pt_BR"
    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("LC_ALL", "C")
    assert i18n_module._os_ui_language() == "es_MX"


def test_setup_locale_with_unknown_lang_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    # A language with no catalog falls back to NullTranslations without raising.
    setup_locale("xx-YY")
    assert _("anything") == "anything"


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_catalog_exists(locale: str) -> None:
    assert _po_path(locale).is_file()


def test_catalogs_share_the_same_msgids() -> None:
    # A string added to one catalog (e.g. after `make i18n-update`) must reach all of them.
    msgids = {locale: {msgid for msgid, _msgstr in _message_pairs(locale)} for locale in ALL_LOCALES}
    reference = msgids["en"]
    for locale, ids in msgids.items():
        assert ids == reference, f"{locale}: missing={sorted(reference - ids)} extra={sorted(ids - reference)}"


def test_catalogs_cover_every_source_string() -> None:
    """The catalogs must match what the code actually marks with `_()`/`N_()`: comparing
    catalogs only with each other lets a new string ship untranslated in all three."""
    app_dir = Path(i18n_module.__file__).parent.parent
    with (app_dir / "i18n" / "babel.cfg").open(encoding="utf-8") as cfg:
        method_map, options_map = parse_mapping_cfg(cfg)
    source = {
        msg
        for _file, _line, msg, _comments, _ctx in extract_from_dir(str(app_dir), method_map, options_map)
        if isinstance(msg, str)
    }
    catalog = {msgid for msgid, _msgstr in _message_pairs("en")}
    assert source == catalog, (
        "run `make i18n-extract && make i18n-update` and translate: "
        f"missing={sorted(source - catalog)} obsolete={sorted(catalog - source)}"
    )


@pytest.mark.parametrize("locale", TRANSLATED_LOCALES)
def test_every_msgid_is_translated(locale: str) -> None:
    catalog = _load_catalog(locale)
    assert not catalog.fuzzy, f"{locale}: catalog header is marked fuzzy (pybabel compile would skip it)"
    fuzzy = [message.id for message in catalog if message.id and message.fuzzy]
    assert not fuzzy, f"{locale}: {len(fuzzy)} fuzzy message(s): {fuzzy}"
    untranslated = [msgid for msgid, msgstr in _message_pairs(locale) if not msgstr.strip()]
    assert not untranslated, f"{locale}: {len(untranslated)} untranslated message(s): {untranslated}"


@pytest.mark.parametrize("kind", sorted(_PRESERVED_TOKENS))
@pytest.mark.parametrize("locale", TRANSLATED_LOCALES)
def test_translation_preserves_tokens(locale: str, kind: str) -> None:
    extract = _PRESERVED_TOKENS[kind]
    mismatches = [
        (msgid, msgstr)
        for msgid, msgstr in _message_pairs(locale)
        if Counter(extract(msgid)) != Counter(extract(msgstr))
    ]
    assert not mismatches, f"{locale}: {kind} changed in {len(mismatches)} message(s): {mismatches}"


@pytest.mark.parametrize("locale", TRANSLATED_LOCALES)
def test_translation_preserves_leading_and_trailing_whitespace(locale: str) -> None:
    mismatches = [(msgid, msgstr) for msgid, msgstr in _message_pairs(locale) if _edges(msgid) != _edges(msgstr)]
    assert not mismatches, f"{locale}: whitespace changed in {len(mismatches)} message(s): {mismatches}"
