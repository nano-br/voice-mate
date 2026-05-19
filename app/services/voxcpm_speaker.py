from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.services.audio_player import AudioPlayer


class VoxCPMSpeaker:
    """Speaker baseado em VoxCPM2 (OpenBMB).

    Voice design por descrição em parênteses ("(jovem mulher...) texto"),
    streaming via `generate_streaming` quando disponível, fallback para
    geração one-shot. Cancelamento instantâneo via `stop()` (aborta player
    e para de consumir o gerador).
    """

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._player = AudioPlayer()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._closed = False
        self._model: Any = self._load_model()
        self._sample_rate: int = int(self._model.tts_model.sample_rate)
        self._player.start(self._sample_rate)

    def is_active(self) -> bool:
        return not self._closed

    def speak(self, text: str) -> None:
        if self._closed or not text.strip():
            return
        self._stop_event.clear()
        prompt = f"({self._config.voice_description}) {text}"
        collected: list[NDArray[np.float32]] = []
        try:
            if self._config.streaming and hasattr(self._model, "generate_streaming"):
                self._speak_streaming(prompt, collected)
            else:
                self._speak_oneshot(prompt, collected)
            self._player.drain(timeout=None)
        except Exception as exc:  # noqa: BLE001
            print(f"[VoxCPMSpeaker] erro ao falar: {exc}", file=sys.stderr)
            self._player.abort()
            return
        if self._stop_event.is_set():
            return
        if self._config.save_audio_dir and collected:
            self._save_audio(collected)

    def stop(self) -> None:
        self._stop_event.set()
        self._player.abort()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._player.close()

    def _load_model(self) -> Any:  # noqa: ANN401 — modelo VoxCPM é dinâmico
        from voxcpm import VoxCPM

        self._warn_if_torch_lacks_cuda()
        kwargs: dict[str, Any] = {
            "load_denoiser": self._config.denoise,
            "optimize": self._config.optimize,
        }
        if self._config.device != "auto":
            kwargs["device"] = self._config.device
        if self._config.cache_dir is not None:
            kwargs["cache_dir"] = self._config.cache_dir
        print("[VoxCPMSpeaker] Carregando VoxCPM2 (primeira execução baixa pesos)...")
        model = VoxCPM.from_pretrained("openbmb/VoxCPM2", **kwargs)
        print("[VoxCPMSpeaker] Modelo pronto.")
        return model

    @staticmethod
    def _warn_if_torch_lacks_cuda() -> None:
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            return
        print(
            "[VoxCPMSpeaker] ⚠ PyTorch sem CUDA detectado — VoxCPM rodará em CPU "
            "(síntese ~50-100× mais lenta que GPU).",
            file=sys.stderr,
        )
        print(
            "[VoxCPMSpeaker]   Para habilitar GPU, reinstale o PyTorch com build CUDA:",
            file=sys.stderr,
        )
        print(
            "[VoxCPMSpeaker]   poetry run pip install --upgrade --index-url "
            "https://download.pytorch.org/whl/cu128 torch torchaudio",
            file=sys.stderr,
        )

    def _speak_streaming(self, prompt: str, collected: list[NDArray[np.float32]]) -> None:
        # após abort, recria o stream para o próximo speak
        self._player.start(self._sample_rate)
        chunks = self._model.generate_streaming(
            text=prompt,
            cfg_value=self._config.cfg_value,
            inference_timesteps=self._config.inference_timesteps,
            normalize=self._config.normalize,
        )
        for chunk in chunks:
            if self._stop_event.is_set():
                break
            arr = self._as_float32(chunk)
            collected.append(arr)
            self._player.feed(arr)

    def _speak_oneshot(self, prompt: str, collected: list[NDArray[np.float32]]) -> None:
        self._player.start(self._sample_rate)
        wav = self._model.generate(
            text=prompt,
            cfg_value=self._config.cfg_value,
            inference_timesteps=self._config.inference_timesteps,
            normalize=self._config.normalize,
        )
        arr = self._as_float32(wav)
        collected.append(arr)
        if not self._stop_event.is_set():
            self._player.feed(arr)

    @staticmethod
    def _as_float32(audio: Any) -> NDArray[np.float32]:  # noqa: ANN401 — chunk vem do SDK
        arr = np.asarray(audio, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        return arr

    def _save_audio(self, chunks: list[NDArray[np.float32]]) -> None:
        try:
            import soundfile as sf
        except ImportError:
            print(
                "[VoxCPMSpeaker] soundfile não instalado — não foi possível salvar áudio.",
                file=sys.stderr,
            )
            return
        directory = Path(self._config.save_audio_dir or ".")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"voxcpm_{int(time.time() * 1000)}.wav"
        full = np.concatenate(chunks)
        sf.write(str(path), full, self._sample_rate)
        print(f"[VoxCPMSpeaker] Áudio salvo em {path}")
