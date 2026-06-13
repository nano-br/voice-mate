"""OpenAIWhisperBackend.warmup — sem carregar o modelo real."""

from __future__ import annotations

import numpy as np
import pytest
from numpy.typing import NDArray

from app.features.openai_whisper.backend import OpenAIWhisperBackend


def _make_backend() -> OpenAIWhisperBackend:
    # Evita o __init__ pesado (import whisper + load_model).
    return OpenAIWhisperBackend.__new__(OpenAIWhisperBackend)


def test_warmup_calls_transcribe_once() -> None:
    backend = _make_backend()
    calls: list[NDArray[np.float32]] = []
    backend.transcribe = calls.append  # type: ignore[method-assign,assignment]
    backend.warmup()
    assert len(calls) == 1
    assert calls[0].shape == (16000,)  # 1s de silêncio @ 16kHz


def test_warmup_swallows_exception(capsys: pytest.CaptureFixture[str]) -> None:
    backend = _make_backend()

    def _boom(_audio: NDArray[np.float32]) -> str:
        raise RuntimeError("miopen explodiu")

    backend.transcribe = _boom  # type: ignore[method-assign,assignment]
    backend.warmup()  # não pode levantar
    assert "warmup do openai-whisper falhou" in capsys.readouterr().err
