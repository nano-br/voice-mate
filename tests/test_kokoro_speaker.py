"""KokoroSpeaker: lazy load, per-chunk streaming into the player, language map.

Does not load the real KPipeline — injects a fake pipeline (chunk generator) and a
fake AudioPlayer that records the feeds.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.features.tts.kokoro_speaker import _KOKORO_LANG_CODES, KokoroSpeaker


class FakePlayer:
    def __init__(self) -> None:
        self.fed: list[NDArray[np.float32]] = []
        self.started_rate: int | None = None
        self.aborted = 0
        self.closed = 0
        self.drained = 0

    def ensure_started(self, sample_rate: int) -> None:
        self.started_rate = sample_rate

    def feed(self, chunk: NDArray[np.float32]) -> None:
        self.fed.append(chunk)

    def drain(self, timeout: float | None = None) -> bool:
        self.drained += 1
        return True

    def abort(self) -> None:
        self.aborted += 1

    def close(self) -> None:
        self.closed += 1


class FakePipeline:
    """Mimics the KPipeline: a callable that returns a generator of (gs, ps, audio)."""

    def __init__(self, chunks: list[NDArray[np.float32]]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, str]] = []

    def __call__(self, text: str, voice: str) -> Iterator[tuple[int, str, NDArray[np.float32]]]:
        self.calls.append((text, voice))
        for chunk in self.chunks:
            yield (0, "ps", chunk)


def _speaker(config: TTSConfig, pipeline: FakePipeline | None = None) -> tuple[KokoroSpeaker, FakePlayer]:
    speaker = KokoroSpeaker(config)
    player = FakePlayer()
    speaker._player = player  # type: ignore[assignment]
    speaker._pipeline = pipeline  # type: ignore[assignment]
    return speaker, player


def test_lang_code_map_pt_is_p() -> None:
    assert _KOKORO_LANG_CODES["pt"] == "p"
    assert _KOKORO_LANG_CODES["en"] == "a"


def test_auto_device_prefers_cpu() -> None:
    # "auto" → CPU: Kokoro is realtime on the CPU and does not compete for the GPU (kills the crackle).
    speaker = KokoroSpeaker(TTSConfig(device="auto"))
    assert speaker._resolve_device() == "cpu"


def test_explicit_cuda_device_respected() -> None:
    speaker = KokoroSpeaker(TTSConfig(device="cuda"))
    assert speaker._resolve_device() == "cuda:0"


def test_speak_feeds_each_chunk_into_player() -> None:
    chunks = [np.ones(100, dtype=np.float32), np.full(50, 0.5, dtype=np.float32)]
    pipeline = FakePipeline(chunks)
    speaker, player = _speaker(TTSConfig(kokoro_voice="pf_dora"), pipeline)

    speaker.speak("olá mundo")

    assert player.started_rate == 24000
    assert len(player.fed) == 2
    assert pipeline.calls == [("olá mundo", "pf_dora")]


def test_speak_skips_empty_text() -> None:
    pipeline = FakePipeline([np.ones(10, dtype=np.float32)])
    speaker, player = _speaker(TTSConfig(), pipeline)
    speaker.speak("   ")
    assert player.fed == []


def test_stop_aborts_the_player() -> None:
    speaker, player = _speaker(TTSConfig(), FakePipeline([]))
    speaker.stop()
    assert player.aborted == 1
    assert speaker._stop_event.is_set()


def test_speak_breaks_when_stopped_midstream() -> None:
    # Concurrent stop() mid-speech: the 1st chunk plays, the 2nd does not.
    speaker, player = _speaker(TTSConfig())
    chunks = [np.ones(10, dtype=np.float32) for _ in range(4)]

    def _gen(text: str, voice: str) -> Iterator[tuple[int, str, NDArray[np.float32]]]:
        for i, chunk in enumerate(chunks):
            if i == 1:
                speaker._stop_event.set()  # simulates stop() coming from another thread
            yield (0, "ps", chunk)

    class _StoppingPipeline:
        def __call__(self, text: str, voice: str) -> Iterator[tuple[int, str, NDArray[np.float32]]]:
            return _gen(text, voice)

    speaker._pipeline = _StoppingPipeline()  # type: ignore[assignment]
    speaker.speak("oi")
    assert len(player.fed) == 1


def test_close_marks_inactive_and_closes_player() -> None:
    speaker, player = _speaker(TTSConfig(), FakePipeline([]))
    assert speaker.is_active() is True
    speaker.close()
    assert speaker.is_active() is False
    assert player.closed == 1


def test_coerce_handles_torch_like_tensor() -> None:
    class _FakeTensor:
        def __init__(self, arr: NDArray[np.float32]) -> None:
            self._arr = arr

        def detach(self) -> _FakeTensor:
            return self

        def to(self, _device: str) -> _FakeTensor:
            return self

        def numpy(self) -> NDArray[np.float32]:
            return self._arr

    out = KokoroSpeaker._coerce(_FakeTensor(np.ones((2, 3), dtype=np.float32)))
    assert out.ndim == 1 and out.shape == (6,)


def test_warmup_consumes_generator_without_feeding(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = FakePipeline([np.ones(10, dtype=np.float32)])
    speaker, player = _speaker(TTSConfig(), pipeline)
    speaker.warmup()
    # warmup synthesizes "Olá." but does NOT feed the player (no playback)
    assert pipeline.calls == [("Olá.", "pf_dora")]
    assert player.fed == []
