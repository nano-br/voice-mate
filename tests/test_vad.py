"""trim_to_speech: degrades safely and concatenates speech segments."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

import app.features.openai_whisper.vad as vad_mod


def _reset() -> None:
    vad_mod._vad_model = None
    vad_mod._unavailable = False


def test_returns_original_when_silero_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    monkeypatch.setitem(sys.modules, "silero_vad", None)  # import fails
    audio = np.ones(16000, dtype=np.float32)
    out = vad_mod.trim_to_speech(audio)
    assert out is audio
    assert vad_mod._unavailable is True  # flagged so it does not retry


def test_concatenates_speech_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    audio = np.arange(1000, dtype=np.float32)

    fake = types.ModuleType("silero_vad")
    fake.load_silero_vad = lambda: "model"  # type: ignore[attr-defined]
    fake.get_speech_timestamps = lambda t, m, sampling_rate: [  # type: ignore[attr-defined]
        {"start": 100, "end": 200},
        {"start": 500, "end": 550},
    ]

    def _collect(ts: list[dict[str, int]], tensor: object) -> object:
        import torch

        parts = [tensor[seg["start"] : seg["end"]] for seg in ts]  # type: ignore[index]
        return torch.cat(parts)

    fake.collect_chunks = _collect  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "silero_vad", fake)

    out = vad_mod.trim_to_speech(audio)
    assert len(out) == 150  # (200-100) + (550-500)


def test_returns_original_when_no_speech(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset()
    fake = types.ModuleType("silero_vad")
    fake.load_silero_vad = lambda: "model"  # type: ignore[attr-defined]
    fake.get_speech_timestamps = lambda t, m, sampling_rate: []  # type: ignore[attr-defined]
    fake.collect_chunks = lambda ts, t: t  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "silero_vad", fake)

    audio = np.ones(16000, dtype=np.float32)
    assert vad_mod.trim_to_speech(audio) is audio
