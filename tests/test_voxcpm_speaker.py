from __future__ import annotations

import sys
import threading
import time
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from app.core.config import TTSConfig

_fake_state: dict[str, Any] = {}


class FakeTTSModel:
    sample_rate = 24000


class FakeVoxCPMInstance:
    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401 — kwargs livres do SDK
        self.kwargs = kwargs
        self.tts_model = FakeTTSModel()
        _fake_state["last_instance"] = self

    def generate_streaming(
        self,
        text: str,
        cfg_value: float,
        inference_timesteps: int,
        normalize: bool,
    ) -> Iterator[NDArray[np.float32]]:
        _fake_state["last_prompt"] = text
        _fake_state["last_cfg"] = cfg_value
        _fake_state["last_steps"] = inference_timesteps
        _fake_state["last_normalize"] = normalize
        chunks = _fake_state.get("chunks", [np.ones(8, dtype=np.float32)])
        stop_event: threading.Event | None = _fake_state.get("stop_event")
        for chunk in chunks:
            if stop_event is not None and stop_event.is_set():
                return
            delay = _fake_state.get("chunk_delay", 0.0)
            if delay:
                time.sleep(delay)
            yield chunk

    def generate(
        self,
        text: str,
        cfg_value: float,
        inference_timesteps: int,
        normalize: bool,
    ) -> NDArray[np.float32]:
        _fake_state["last_prompt"] = text
        return np.ones(16, dtype=np.float32)


class FakeVoxCPM:
    @classmethod
    def from_pretrained(cls, model_id: str, **kwargs: Any) -> FakeVoxCPMInstance:  # noqa: ANN401
        _fake_state["model_id"] = model_id
        return FakeVoxCPMInstance(**kwargs)


class FakePlayer:
    def __init__(self) -> None:
        self.starts: list[int] = []
        self.fed: list[NDArray[np.float32]] = []
        self.aborts = 0
        self.closes = 0
        self.drain_event = threading.Event()
        self.drain_event.set()

    def start(self, sample_rate: int) -> None:
        self.starts.append(sample_rate)

    def feed(self, chunk: NDArray[np.float32]) -> None:
        self.fed.append(chunk)

    def drain(self, timeout: float | None = None) -> bool:
        return self.drain_event.wait(timeout=timeout)

    def abort(self) -> None:
        self.aborts += 1
        self.drain_event.set()

    def close(self) -> None:
        self.closes += 1


@pytest.fixture
def fake_voxcpm_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    _fake_state.clear()
    module = types.ModuleType("voxcpm")
    module.VoxCPM = FakeVoxCPM  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "voxcpm", module)
    monkeypatch.setattr("app.services.voxcpm_speaker.AudioPlayer", FakePlayer)
    return _fake_state


def _make_speaker(**overrides: Any) -> tuple[Any, FakePlayer]:  # noqa: ANN401
    from app.services.voxcpm_speaker import VoxCPMSpeaker

    defaults: dict[str, Any] = {
        "enabled": True,
        "engine": "voxcpm",
        "voice_description": "Voz teste",
        "cfg_value": 2.5,
        "inference_timesteps": 8,
        "device": "cuda",
        "streaming": True,
        "save_audio_dir": None,
        "optimize": False,
        "cache_dir": None,
        "normalize": True,
        "denoise": False,
    }
    defaults.update(overrides)
    config = TTSConfig(**defaults)
    speaker = VoxCPMSpeaker(config)
    return speaker, speaker._player  # type: ignore[attr-defined,return-value]


def test_construct_loads_model_with_kwargs(fake_voxcpm_env: dict[str, Any]) -> None:
    speaker, player = _make_speaker()
    instance = fake_voxcpm_env["last_instance"]

    assert fake_voxcpm_env["model_id"] == "openbmb/VoxCPM2"
    assert instance.kwargs["load_denoiser"] is False
    assert instance.kwargs["optimize"] is False
    assert instance.kwargs["device"] == "cuda"
    assert speaker.is_active() is True
    assert player.starts == [24000]


def test_speak_builds_prompt_and_feeds_player(fake_voxcpm_env: dict[str, Any]) -> None:
    fake_voxcpm_env["chunks"] = [
        np.ones(4, dtype=np.float32),
        np.ones(4, dtype=np.float32) * 2,
    ]
    speaker, player = _make_speaker(voice_description="Mulher jovem")

    speaker.speak("olá mundo")

    assert fake_voxcpm_env["last_prompt"] == "(Mulher jovem) olá mundo"
    assert fake_voxcpm_env["last_cfg"] == 2.5
    assert fake_voxcpm_env["last_steps"] == 8
    assert fake_voxcpm_env["last_normalize"] is True
    assert len(player.fed) == 2


def test_stop_aborts_player_and_breaks_streaming(
    fake_voxcpm_env: dict[str, Any],
) -> None:
    stop_event = threading.Event()
    fake_voxcpm_env["stop_event"] = stop_event
    fake_voxcpm_env["chunks"] = [np.ones(4, dtype=np.float32)] * 50
    fake_voxcpm_env["chunk_delay"] = 0.01

    speaker, player = _make_speaker()

    worker = threading.Thread(target=speaker.speak, args=("texto longo",), daemon=True)
    worker.start()
    time.sleep(0.05)
    # marca o stop_event do gerador para simular cancelamento real do consumidor
    speaker.stop()
    stop_event.set()
    worker.join(timeout=2.0)

    assert player.aborts >= 1
    assert len(player.fed) < 50


def test_oneshot_when_streaming_disabled(fake_voxcpm_env: dict[str, Any]) -> None:
    speaker, player = _make_speaker(streaming=False)

    speaker.speak("oi")

    assert len(player.fed) == 1
    assert fake_voxcpm_env["last_prompt"] == "(Voz teste) oi"


def test_empty_text_does_nothing(fake_voxcpm_env: dict[str, Any]) -> None:
    speaker, player = _make_speaker()
    initial = len(player.fed)
    speaker.speak("   ")
    assert len(player.fed) == initial


def test_close_marks_inactive_and_closes_player(fake_voxcpm_env: dict[str, Any]) -> None:
    speaker, player = _make_speaker()
    assert speaker.is_active() is True

    speaker.close()

    assert speaker.is_active() is False
    assert player.closes == 1


def test_save_audio_dir_writes_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_voxcpm_env: dict[str, Any]
) -> None:
    written: list[tuple[str, NDArray[np.float32], int]] = []

    fake_soundfile = types.ModuleType("soundfile")

    def fake_write(path: str, data: NDArray[np.float32], sample_rate: int) -> None:
        written.append((path, data, sample_rate))

    fake_soundfile.write = fake_write  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)

    fake_voxcpm_env["chunks"] = [np.ones(4, dtype=np.float32)]
    speaker, _ = _make_speaker(save_audio_dir=str(tmp_path))

    speaker.speak("oi")

    assert len(written) == 1
    path, data, sr = written[0]
    assert path.endswith(".wav")
    assert sr == 24000
    assert data.shape == (4,)
