from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import get_args

import pytest
from babel import Locale
from babel.messages.catalog import Catalog
from babel.messages.extract import extract_from_dir
from babel.messages.frontend import parse_mapping_cfg
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po

import app.i18n as i18n_module
from app.companion.contract import UiLanguage
from app.i18n import _, active_language, catalog_for, set_language, setup_locale

_SOURCE_LOCALES_DIR = Path(i18n_module.__file__).parent / "locales"
_DOMAIN = "voicemate"

# Every user-facing string must exist in these catalogs.
ALL_LOCALES = ("en", "pt_BR", "es", "ru", "zh_CN")
# `en` msgstrs are intentionally empty (the English msgid is the source text);
# these locales must translate every msgid.
TRANSLATED_LOCALES = ("pt_BR", "es", "ru", "zh_CN")
_DASHES = (chr(0x2014), chr(0x2013))  # em dash, en dash
CALLING_CLAUDE = "[VoiceMate] 🤖 Calling Claude..."
CALLING_CLAUDE_RU = "[VoiceMate] 🤖 Обращение к Claude..."


def _symbols(text: str) -> list[str]:
    """Emojis and pictographic symbols (⚠ ✓ ✗ ❌ 🎙 ☕ ↳ ...): Unicode category "So"."""
    return [char for char in text if unicodedata.category(char) == "So"]


# Tokens that must survive translation unchanged (compared as multisets, so the
# translation is free to reorder them).
_PRESERVED_TOKENS: dict[str, Callable[[str], list[str]]] = {
    "brace placeholder": re.compile(r"\{[^{}]*\}").findall,
    "printf placeholder": re.compile(r"%(?:\([A-Za-z_]\w*\))?[#0+-]?\d*(?:\.\d+)?[sdifrx]").findall,
    "backtick-quoted command": re.compile(r"`[^`]+`").findall,
    # ASCII only: Chinese text may follow a URL without a space ("http://...上的").
    "URL": re.compile(r"https?://[^\s)\x80-\U0010ffff]*[^\s).,;:\x80-\U0010ffff]").findall,
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
    # A missing catalog fails `test_catalog_exists` (and the tests that need it), not
    # every test of this module.
    for locale in (locale for locale in ALL_LOCALES if _po_path(locale).is_file()):
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
        ("ru", "ru", CALLING_CLAUDE_RU),
        ("en", "en", "[VoiceMate] 🤖 Calling Claude..."),
    ],
)
def test_set_language_loads_the_companion_language_and_ignores_the_env_var(
    monkeypatch: pytest.MonkeyPatch, language: UiLanguage, expected_lang: str, expected_text: str
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
        ("ru_RU", "ru"),
        ("ru_UA", "ru"),
        ("zh_CN", "zh_CN"),
        ("zh_SG", "zh_CN"),
        ("zh_TW", "zh_CN"),  # the only Chinese catalog reads better than English
        ("zh_HK", "zh_CN"),
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
    assert catalog_for("auto") == expected_lang


@pytest.mark.parametrize("language", get_args(UiLanguage))
def test_catalog_for_names_the_catalog_set_language_loads(language: UiLanguage) -> None:
    """The companion compares it with active_language() to decide on a restart."""
    set_language(language)
    assert catalog_for(language) == active_language()


def test_every_ui_language_has_a_catalog() -> None:
    """Each choice of the companion's Language combo selects one of the shipped catalogs."""
    explicit = set(get_args(UiLanguage)) - {"auto"}
    assert set(i18n_module._UI_LANGUAGE_CATALOGS) == explicit
    assert set(i18n_module._UI_LANGUAGE_CATALOGS.values()) == set(ALL_LOCALES)
    assert set(i18n_module._OS_LANGUAGE_CATALOGS.values()) == set(ALL_LOCALES)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("pt-BR", "pt_BR"),
        ("pt-PT", "pt_BR"),
        ("es-419", "es"),
        ("ru", "ru"),
        ("ru-RU", "ru"),
        ("ru_RU.UTF-8", "ru"),
        ("zh-CN", "zh_CN"),
        ("zh", "zh_CN"),
        ("zh-TW", "zh_CN"),
        ("zh_HK.UTF-8", "zh_CN"),
        ("en-GB", "en"),
        ("fr-FR", "fr_FR"),  # no catalog: falls through to the next candidate
    ],
)
def test_engine_language_names_pick_their_catalog(requested: str, expected: str) -> None:
    """VOICEMATE_LANG and --output-lang take BCP-47 or POSIX names (`zh-TW`, `ru_RU.UTF-8`)."""
    assert i18n_module._catalog_name(requested) == expected


@pytest.mark.parametrize(
    ("requested", "expected_lang"),
    [("ru-RU", "ru"), ("zh-TW", "zh_CN"), ("zh-CN", "zh_CN"), ("pt-PT", "pt_BR")],
)
def test_env_var_regional_names_load_the_language_catalog(
    monkeypatch: pytest.MonkeyPatch, requested: str, expected_lang: str
) -> None:
    monkeypatch.setenv("VOICEMATE_LANG", requested)
    setup_locale("en")
    assert active_language() == expected_lang


def test_setup_locale_ru_translates_known_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("ru")
    assert _(CALLING_CLAUDE) == CALLING_CLAUDE_RU


def test_setup_locale_zh_cn_translates_known_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_LANG", raising=False)
    setup_locale("zh-CN")
    assert active_language() == "zh_CN"
    translated = _(CALLING_CLAUDE)
    assert translated != CALLING_CLAUDE
    assert any("\u4e00" <= char <= "\u9fff" for char in translated), translated


def test_catalog_for_is_english_without_a_compiled_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(i18n_module, "_LOCALES_DIR", tmp_path)
    assert catalog_for("es") == "en"
    set_language("es")
    assert active_language() == "en"


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, None),  # no locale at all: the C locale
        ({"LANGUAGE": "pt_BR:en"}, None),  # LANGUAGE alone does not count
        ({"LANG": "es_MX.UTF-8"}, "es_MX"),
        ({"LANG": "es_MX.UTF-8", "LANGUAGE": "pt_BR:en"}, "pt_BR"),  # LANGUAGE wins over a real locale
        ({"LANG": "es_MX.UTF-8", "LANGUAGE": ""}, "es_MX"),
        ({"LANG": "es_MX.UTF-8", "LC_MESSAGES": "de_DE@euro"}, "de_DE"),  # LC_MESSAGES before LANG
        ({"LANG": "es_MX", "LC_ALL": "C", "LANGUAGE": "pt_BR"}, None),  # LC_ALL=C beats LANGUAGE
        ({"LANG": "es_MX", "LC_ALL": "C.UTF-8", "LANGUAGE": "pt_BR"}, None),
        ({"LANG": "es_MX", "LC_ALL": "POSIX", "LANGUAGE": "pt_BR"}, None),
    ],
)
def test_posix_ui_language_follows_gnu_gettext(environ: dict[str, str], expected: str | None) -> None:
    """The C locale (or none) means English and LANGUAGE is ignored; otherwise LANGUAGE's
    first entry wins over the messages locale."""
    assert i18n_module._posix_ui_language(environ) == expected


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


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_no_em_or_en_dash_in_translations(locale: str) -> None:
    """House style: commas, colons or parentheses instead of em/en dashes, in every language."""
    offenders = [msgstr for _msgid, msgstr in _message_pairs(locale) if any(dash in msgstr for dash in _DASHES)]
    assert not offenders, f"{locale}: {offenders}"


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_catalog_header_names_its_language(locale: str) -> None:
    """`Language:` must be the catalog's own language (zh_CN and zh_Hans_CN are the same
    locale), and Russian needs its three plural forms."""
    headers = dict(_load_catalog(locale).mime_headers)
    assert Locale.parse(headers["Language"]) == Locale.parse(locale), f"{locale}: {headers['Language']!r}"
    if locale == "ru":
        assert headers["Plural-Forms"].startswith("nplurals=3;"), headers["Plural-Forms"]


@pytest.mark.parametrize("locale", TRANSLATED_LOCALES)
def test_translation_preserves_leading_and_trailing_whitespace(locale: str) -> None:
    mismatches = [(msgid, msgstr) for msgid, msgstr in _message_pairs(locale) if _edges(msgid) != _edges(msgstr)]
    assert not mismatches, f"{locale}: whitespace changed in {len(mismatches)} message(s): {mismatches}"
