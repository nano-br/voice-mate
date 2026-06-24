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

    def generate_streaming(self, **kwargs: Any) -> Iterator[NDArray[np.float32]]:  # noqa: ANN401
        _fake_state["last_call"] = "streaming"
        _fake_state["last_kwargs"] = kwargs
        chunks = _fake_state.get("chunks", [np.ones(8, dtype=np.float32)])
        stop_event: threading.Event | None = _fake_state.get("stop_event")
        for chunk in chunks:
            if stop_event is not None and stop_event.is_set():
                return
            delay = _fake_state.get("chunk_delay", 0.0)
            if delay:
                time.sleep(delay)
            yield chunk

    def generate(self, **kwargs: Any) -> NDArray[np.float32]:  # noqa: ANN401
        _fake_state["last_call"] = "oneshot"
        _fake_state["last_kwargs"] = kwargs
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
def fake_voxcpm_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    _fake_state.clear()
    # garante que o auto-seed não escolha o cache real do usuário
    _fake_state["default_cache_dir"] = str(tmp_path / "voxcpm_cache")
    module = types.ModuleType("voxcpm")
    module.VoxCPM = FakeVoxCPM  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "voxcpm", module)
    monkeypatch.setattr("app.features.tts.voxcpm_speaker.create_audio_player", FakePlayer)
    return _fake_state


def _make_speaker(**overrides: Any) -> tuple[Any, FakePlayer]:  # noqa: ANN401
    from app.features.tts.voxcpm_speaker import VoxCPMSpeaker

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
        # default off para não interferir com testes que não focam em seed
        "voice_seed_mode": "off",
        "voice_seed_cache_dir": _fake_state.get("default_cache_dir"),
    }
    defaults.update(overrides)
    config = TTSConfig(**defaults)
    speaker = VoxCPMSpeaker(config)
    return speaker, speaker._player  # type: ignore[attr-defined,return-value]


def test_lazy_load_defers_model_until_first_speak(fake_voxcpm_env: dict[str, Any]) -> None:
    speaker, _ = _make_speaker()
    # Lazy: a construção NÃO carrega o modelo (sem custo de VRAM até falar).
    assert "last_instance" not in fake_voxcpm_env
    assert speaker.is_active() is True

    # A primeira fala carrega o modelo com os kwargs corretos.
    speaker.speak("olá")
    instance = fake_voxcpm_env["last_instance"]
    assert fake_voxcpm_env["model_id"] == "openbmb/VoxCPM2"
    assert instance.kwargs["load_denoiser"] is False
    assert instance.kwargs["optimize"] is False
    assert instance.kwargs["device"] == "cuda"
    assert speaker.is_active() is True


def test_speak_builds_prompt_and_feeds_player(fake_voxcpm_env: dict[str, Any]) -> None:
    fake_voxcpm_env["chunks"] = [
        np.ones(4, dtype=np.float32),
        np.ones(4, dtype=np.float32) * 2,
    ]
    speaker, player = _make_speaker(voice_description="Mulher jovem")

    speaker.speak("olá mundo")

    kwargs = fake_voxcpm_env["last_kwargs"]
    assert kwargs["text"] == "(Mulher jovem) olá mundo"
    assert kwargs["cfg_value"] == 2.5
    assert kwargs["inference_timesteps"] == 8
    assert kwargs["normalize"] is True
    assert "prompt_wav_path" not in kwargs  # mode == off
    assert len(player.fed) == 2
    assert player.starts == [24000]


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
    speaker.stop()
    stop_event.set()
    worker.join(timeout=2.0)

    assert player.aborts >= 1
    assert len(player.fed) < 50


def test_oneshot_when_streaming_disabled(fake_voxcpm_env: dict[str, Any]) -> None:
    speaker, player = _make_speaker(streaming=False)

    speaker.speak("oi")

    assert len(player.fed) == 1
    assert fake_voxcpm_env["last_call"] == "oneshot"
    assert fake_voxcpm_env["last_kwargs"]["text"] == "(Voz teste) oi"


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


# ─── Voice seed modes ──────────────────────────────────────────────────────


def _install_fake_soundfile(monkeypatch: pytest.MonkeyPatch, written: list[Any]) -> None:
    fake_soundfile = types.ModuleType("soundfile")

    def fake_write(path: str, data: NDArray[np.float32], sample_rate: int) -> None:
        written.append((path, data, sample_rate))

    fake_soundfile.write = fake_write  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)


def test_voice_seed_auto_first_call_has_no_seed_and_persists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_voxcpm_env: dict[str, Any],
) -> None:
    written: list[Any] = []
    _install_fake_soundfile(monkeypatch, written)

    cache_dir = tmp_path / "seedcache"
    fake_voxcpm_env["chunks"] = [np.ones(4, dtype=np.float32)]
    speaker, _ = _make_speaker(
        voice_seed_mode="auto",
        voice_seed_cache_dir=str(cache_dir),
        voice_description="Mulher jovem",
    )

    speaker.speak("primeira fala")

    kwargs = fake_voxcpm_env["last_kwargs"]
    assert "prompt_wav_path" not in kwargs
    assert "prompt_text" not in kwargs
    # Sem seed → modo voice design: descrição entra entre parênteses
    assert kwargs["text"] == "(Mulher jovem) primeira fala"
    # auto-seed deve ter sido salvo (wav + txt)
    assert any(p[0].endswith("voice_seed.wav") for p in written)
    seed_text_path = cache_dir / "voice_seed.txt"
    assert seed_text_path.exists()
    # txt persistido é a fala pura, sem a descrição
    assert seed_text_path.read_text(encoding="utf-8") == "primeira fala"


def test_voice_seed_auto_second_call_uses_persisted_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_voxcpm_env: dict[str, Any],
) -> None:
    written: list[Any] = []
    _install_fake_soundfile(monkeypatch, written)

    cache_dir = tmp_path / "seedcache"
    fake_voxcpm_env["chunks"] = [np.ones(4, dtype=np.float32)]
    speaker, _ = _make_speaker(
        voice_seed_mode="auto",
        voice_seed_cache_dir=str(cache_dir),
        voice_description="Mulher jovem",
    )

    speaker.speak("primeira fala")
    speaker.speak("segunda fala")

    kwargs = fake_voxcpm_env["last_kwargs"]
    assert kwargs["prompt_wav_path"].endswith("voice_seed.wav")
    assert kwargs["prompt_text"] == "primeira fala"
    # Em modo cloning, o texto a sintetizar NÃO pode conter a descrição entre
    # parênteses — senão o modelo lê a descrição em voz alta.
    assert kwargs["text"] == "segunda fala"
    assert "Mulher jovem" not in kwargs["text"]


def test_voice_seed_fixed_passes_user_provided_wav(
    tmp_path: Path,
    fake_voxcpm_env: dict[str, Any],
) -> None:
    seed_wav = tmp_path / "minha-voz.wav"
    seed_wav.write_bytes(b"\x00" * 16)

    fake_voxcpm_env["chunks"] = [np.ones(4, dtype=np.float32)]
    speaker, _ = _make_speaker(
        voice_seed_mode="fixed",
        voice_seed_path=str(seed_wav),
        voice_seed_text="amostra minha",
        voice_description="Voz qualquer",
    )

    speaker.speak("olá")

    kwargs = fake_voxcpm_env["last_kwargs"]
    assert kwargs["prompt_wav_path"] == str(seed_wav)
    assert kwargs["prompt_text"] == "amostra minha"
    # Em fixed/cloning, a descrição não vai pro texto
    assert kwargs["text"] == "olá"
    assert "Voz qualquer" not in kwargs["text"]


def test_voice_seed_off_never_passes_seed(fake_voxcpm_env: dict[str, Any]) -> None:
    fake_voxcpm_env["chunks"] = [np.ones(4, dtype=np.float32)]
    speaker, _ = _make_speaker(voice_seed_mode="off")

    speaker.speak("a")
    speaker.speak("b")

    kwargs = fake_voxcpm_env["last_kwargs"]
    assert "prompt_wav_path" not in kwargs
    assert "prompt_text" not in kwargs


def test_voice_seed_fixed_requires_path_and_text(fake_voxcpm_env: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="voice_seed_path e voice_seed_text"):
        _make_speaker(voice_seed_mode="fixed", voice_seed_path=None, voice_seed_text=None)
