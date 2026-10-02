"""`dictation_language` -> the engine's `--transcription-language` and `--output-lang`."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

import app.i18n as i18n_module
from app.companion.contract import CompanionSettings, DictationLanguage, UiLanguage
from app.companion.controller import default_backend
from app.companion.dictation import EngineLanguage, engine_language, interface_language, resolve_engine_language
from app.companion.supervisor.local import LocalBackend

INTERFACE_CODES = [
    ("pt-BR", "pt", "pt-BR"),
    ("en", "en", "en"),
    ("es", "es", "es"),
    ("ru", "ru", "ru"),
    ("zh-CN", "zh", "zh-CN"),
]


@pytest.mark.parametrize(("ui", "transcription", "output"), INTERFACE_CODES)
def test_interface_follows_the_ui_language(ui: UiLanguage, transcription: str, output: str) -> None:
    # An explicit UI language wins over whatever catalog is loaded.
    assert resolve_engine_language("interface", ui, "en") == EngineLanguage(transcription, output)


@pytest.mark.parametrize(
    ("catalog", "transcription", "output"),
    [("pt_BR", "pt", "pt-BR"), ("en", "en", "en"), ("es", "es", "es"), ("ru", "ru", "ru"), ("zh_CN", "zh", "zh-CN")],
)
def test_interface_with_the_ui_on_auto_uses_the_os_catalog(catalog: str, transcription: str, output: str) -> None:
    assert resolve_engine_language("interface", "auto", catalog) == EngineLanguage(transcription, output)


def test_an_unknown_catalog_means_english() -> None:
    assert interface_language("auto", "fr_FR") == "en"
    assert resolve_engine_language("interface", "auto", "fr_FR") == EngineLanguage("en", "en")


@pytest.mark.parametrize(("ui", "_t", "output"), INTERFACE_CODES)
def test_auto_lets_whisper_detect_and_keeps_claude_in_the_ui_language(ui: UiLanguage, _t: str, output: str) -> None:
    assert resolve_engine_language("auto", ui, "en") == EngineLanguage("auto", output)
    assert resolve_engine_language("auto", "auto", {"pt-BR": "pt_BR", "zh-CN": "zh_CN"}.get(ui, ui)) == EngineLanguage(
        "auto", output
    )


@pytest.mark.parametrize(
    ("dictation", "output"),
    [
        ("pt", "pt-BR"),
        ("en", "en"),
        ("es", "es"),
        ("ru", "ru"),
        ("zh", "zh-CN"),
        ("fr", "fr"),
        ("de", "de"),
        ("it", "it"),
        ("ja", "ja"),
    ],
)
def test_a_pinned_language_sets_both_flags_whatever_the_ui_says(dictation: DictationLanguage, output: str) -> None:
    assert resolve_engine_language(dictation, "es", "es") == EngineLanguage(dictation, output)
    assert resolve_engine_language(dictation, "auto", "zh_CN") == EngineLanguage(dictation, output)


def test_every_option_resolves_to_values_the_engine_accepts() -> None:
    from app.cli.args import parse_args

    for dictation in get_args(DictationLanguage):
        for ui in get_args(UiLanguage):
            language = resolve_engine_language(dictation, ui, "en")
            parsed = parse_args(language.cli_args().split())
            assert parsed.transcription_language == language.transcription
            assert parsed.output_lang == language.output


def test_cli_args_text() -> None:
    assert EngineLanguage("pt", "pt-BR").cli_args() == "--transcription-language pt --output-lang pt-BR"


def test_engine_language_resolves_the_os_language_without_a_loaded_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The controller builds its backend before main.py calls `set_language`: nothing is loaded
    # yet (English, the module default) while the OS speaks Spanish.
    monkeypatch.setattr(i18n_module, "_active_language", "en")
    monkeypatch.setattr("app.companion.dictation.catalog_for", lambda language: "es")
    assert engine_language(CompanionSettings()) == EngineLanguage("es", "es")
    assert engine_language(CompanionSettings(dictation_language="auto")) == EngineLanguage("auto", "es")
    local = default_backend(CompanionSettings(engine_mode="local"), tmp_path)
    assert isinstance(local, LocalBackend)
    assert local.spawn_command("x")[-1].endswith('--transcription-language es --output-lang es"')
    local.close()


def test_an_explicit_ui_language_does_not_consult_the_catalogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.companion.dictation.catalog_for", lambda language: "ru")
    assert engine_language(CompanionSettings(language="es")) == EngineLanguage("es", "es")
    assert engine_language(CompanionSettings(dictation_language="en", language="es")) == EngineLanguage("en", "en")
    assert engine_language(CompanionSettings(language="zh-CN")) == EngineLanguage("zh", "zh-CN")
