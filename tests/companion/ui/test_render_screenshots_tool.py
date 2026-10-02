"""tools/render_screenshots.py: its tables match the app (no rendering here, it is slow)."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

pytest.importorskip("PySide6")

from app.companion.contract import TrayState, UiLanguage  # noqa: E402
from tools import render_screenshots  # noqa: E402

LOCALES = Path(__file__).resolve().parents[3] / "app" / "i18n" / "locales"


def test_one_screenshot_language_per_shipped_catalog() -> None:
    shipped = {po.parents[1].name for po in LOCALES.glob("*/LC_MESSAGES/voicemate.po")}
    assert set(render_screenshots.LANGUAGES.values()) == shipped
    assert set(render_screenshots.LANGUAGES) == set(get_args(UiLanguage)) - {"auto"}


def test_every_language_has_its_own_samples() -> None:
    assert set(render_screenshots.SAMPLES) == set(render_screenshots.LANGUAGES)
    english = render_screenshots.SAMPLES["en"]
    for language, samples in render_screenshots.SAMPLES.items():
        assert samples.recent and samples.pending, language
        assert {kind for kind, _text in samples.recent} == {"transcript", "ai_response"}, language
        if language != "en":
            assert samples.recent != english.recent, f"{language} reuses the English samples"


def test_tray_states_image_shows_every_state_once() -> None:
    assert sorted(render_screenshots.TRAY_STATES) == sorted(get_args(TrayState))


def test_refuses_to_run_outside_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(render_screenshots.sys, "platform", "linux")
    assert render_screenshots.main([]) == 2
