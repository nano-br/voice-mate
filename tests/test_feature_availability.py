from __future__ import annotations

import sys

import pytest

from app.features import claude as claude_feature
from app.features import tts as tts_feature


def test_claude_is_available_when_sdk_present() -> None:
    # The dev env has the extra installed, so the live check must agree.
    assert claude_feature.is_available() is True


def test_claude_is_unavailable_when_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    assert claude_feature.is_available() is False


def test_tts_is_available_when_extras_present() -> None:
    assert tts_feature.is_available() is True


def test_tts_is_unavailable_when_voxcpm_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "voxcpm", None)
    assert tts_feature.is_available() is False


def test_tts_is_unavailable_when_soundfile_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", None)
    assert tts_feature.is_available() is False


def test_tts_build_default_speaker_falls_back_to_null_when_disabled() -> None:
    from app.core.config import TTSConfig

    cfg = TTSConfig(enabled=False)
    speaker = tts_feature.build_default_speaker(cfg)
    assert speaker.is_active() is False


def test_tts_build_default_speaker_falls_back_to_null_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import TTSConfig

    monkeypatch.setitem(sys.modules, "voxcpm", None)
    cfg = TTSConfig(enabled=True)
    speaker = tts_feature.build_default_speaker(cfg)
    assert speaker.is_active() is False
