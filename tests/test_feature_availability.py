from __future__ import annotations

import sys
import types

import pytest

from app.features import claude as claude_feature
from app.features import tts as tts_feature


def _fake_module(name: str) -> types.ModuleType:
    return types.ModuleType(name)


def test_claude_is_available_when_sdk_present() -> None:
    # The dev env has the extra installed, so the live check must agree.
    assert claude_feature.is_available() is True


def test_claude_is_unavailable_when_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    assert claude_feature.is_available() is False


def test_tts_available_for_engine_when_packages_present(monkeypatch: pytest.MonkeyPatch) -> None:
    # Determinístico: injeta os pacotes como presentes, independe do que está instalado.
    monkeypatch.setitem(sys.modules, "soundfile", _fake_module("soundfile"))
    monkeypatch.setitem(sys.modules, "omnivoice", _fake_module("omnivoice"))
    assert tts_feature.is_available("omnivoice") is True


def test_tts_unavailable_when_engine_package_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", _fake_module("soundfile"))
    monkeypatch.setitem(sys.modules, "omnivoice", None)
    assert tts_feature.is_available("omnivoice") is False


def test_tts_voxcpm_engine_checks_voxcpm_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", _fake_module("soundfile"))
    monkeypatch.setitem(sys.modules, "voxcpm", None)
    assert tts_feature.is_available("voxcpm") is False


def test_tts_kokoro_engine_checks_kokoro_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", _fake_module("soundfile"))
    monkeypatch.setitem(sys.modules, "kokoro", _fake_module("kokoro"))
    assert tts_feature.is_available("kokoro") is True
    monkeypatch.setitem(sys.modules, "kokoro", None)
    assert tts_feature.is_available("kokoro") is False


def test_tts_is_unavailable_when_soundfile_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", None)
    assert tts_feature.is_available("omnivoice") is False


def test_tts_none_engine_is_unavailable() -> None:
    assert tts_feature.is_available("none") is False


def test_tts_build_default_speaker_falls_back_to_null_when_disabled() -> None:
    from app.core.config import TTSConfig

    cfg = TTSConfig(enabled=False)
    speaker = tts_feature.build_default_speaker(cfg)
    assert speaker.is_active() is False


def test_tts_build_default_speaker_falls_back_to_null_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import TTSConfig

    monkeypatch.setitem(sys.modules, "omnivoice", None)
    cfg = TTSConfig(enabled=True)  # engine default = omnivoice
    speaker = tts_feature.build_default_speaker(cfg)
    assert speaker.is_active() is False
