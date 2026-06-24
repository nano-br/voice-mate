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


class _FakeModel:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    def transcribe(self, audio: NDArray[np.float32], **kwargs: object) -> dict[str, str]:
        self.kwargs = kwargs
        return {"text": " olá "}


def _configure(backend: OpenAIWhisperBackend, model: _FakeModel) -> None:
    backend._model = model  # type: ignore[attr-defined]
    backend._beam_size = 5  # type: ignore[attr-defined]
    backend._fp16 = True  # type: ignore[attr-defined]
    backend._language = "pt"  # type: ignore[attr-defined]


def test_transcribe_uses_fast_decoding_params() -> None:
    """temperature=0.0 (sem re-decodificar) + condition_on_previous_text=False
    (sem contexto crescente) — os levers de velocidade em áudio longo/ruidoso."""
    backend = _make_backend()
    model = _FakeModel()
    _configure(backend, model)

    text = backend.transcribe(np.zeros(16000, dtype=np.float32))

    assert text == "olá"
    assert model.kwargs["temperature"] == 0.0
    assert model.kwargs["condition_on_previous_text"] is False
    assert model.kwargs["beam_size"] == 5
    assert model.kwargs["language"] == "pt"


def _spy_vad(monkeypatch: pytest.MonkeyPatch, calls: list[int]) -> None:
    import app.features.openai_whisper.vad as vad_mod

    def _spy(audio: NDArray[np.float32], sample_rate: int) -> NDArray[np.float32]:
        calls.append(len(audio))
        return audio

    monkeypatch.setattr(vad_mod, "trim_to_speech", _spy)


def test_short_audio_skips_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    _spy_vad(monkeypatch, calls)
    backend = _make_backend()
    _configure(backend, _FakeModel())
    backend.transcribe(np.zeros(5 * 16000, dtype=np.float32))  # 5s < 20s
    assert calls == []  # VAD não rodou em áudio curto


def test_long_audio_runs_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    _spy_vad(monkeypatch, calls)
    backend = _make_backend()
    _configure(backend, _FakeModel())
    backend.transcribe(np.zeros(30 * 16000, dtype=np.float32))  # 30s > 20s
    assert calls == [30 * 16000]  # VAD rodou em áudio longo
