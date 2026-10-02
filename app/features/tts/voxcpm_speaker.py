from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.features.tts.audio_player import AudioSink, create_audio_player
from app.i18n import _

_AUTO_SEED_WAV_FILENAME = "voice_seed.wav"
_AUTO_SEED_TEXT_FILENAME = "voice_seed.txt"


class VoxCPMSpeaker:
    """VoxCPM2-based speaker (OpenBMB).

    Voice design via a parenthetical description ("(young woman...) text"),
    streaming via `generate_streaming` when available, falling back to one-shot
    generation. Instant cancellation via `stop()` (aborts the player and stops
    consuming the generator).

    Voice consistency modes (`voice_seed_mode`):
      - "auto": the first utterance is saved as a reference WAV and used in the
        following utterances (`prompt_wav_path` + `prompt_text`).
      - "fixed": uses an external WAV provided by the user.
      - "off": each turn re-rolls the voice (varied behavior).
    """

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._player: AudioSink = create_audio_player()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._closed = False
        self._load_failed = False
        self._seed_path: str | None = None
        self._seed_text: str | None = None
        self._auto_seed_cache_dir: Path | None = None
        self._configure_voice_seed()
        # Lazy load: the model (~5-6 GB of VRAM) only loads on the FIRST utterance.
        # Clipboard-only sessions never pay that VRAM/time cost.
        self._model: Any = None
        self._sample_rate: int = 16000

    def is_active(self) -> bool:
        return not self._closed and not self._load_failed

    def warmup(self) -> None:
        """Preload the model outside the 1st turn (called in the background at startup)."""
        if self._closed or self._load_failed:
            return
        self._ensure_model()

    def wait_done(self, timeout: float | None = None) -> bool:  # noqa: ARG002 — speak() already blocks
        return True

    def _ensure_model(self) -> bool:
        """Load the model on demand (1st utterance). Returns False on failure."""
        if self._model is not None:
            return True
        if self._load_failed:
            return False
        try:
            model = self._load_model()
            self._sample_rate = int(model.tts_model.sample_rate)
            self._model = model
            return True
        except Exception as exc:  # noqa: BLE001
            print(_("[VoxCPMSpeaker] ⚠ Failed to load VoxCPM2: {exc}").format(exc=exc), file=sys.stderr)
            self._load_failed = True
            return False

    def speak(self, text: str) -> None:
        if self._closed or not text.strip():
            return
        if not self._ensure_model():
            return
        self._stop_event.clear()
        collected: list[NDArray[np.float32]] = []
        self._log_vram("before")
        mode_label = _("cloning") if self._has_seed() else _("voice-design")
        print(_("[VoxCPMSpeaker] 🔊 Synthesizing voice ({mode})...").format(mode=mode_label))
        started_at = time.monotonic()
        try:
            if self._config.streaming and hasattr(self._model, "generate_streaming"):
                self._speak_streaming(text, collected)
            else:
                self._speak_oneshot(text, collected)
            drained = self._player.drain(timeout=self._config.drain_timeout_seconds)
            if not drained:
                print(
                    _(
                        "[VoxCPMSpeaker] ⚠ AudioPlayer drain timed out — "
                        "flow recovered, audio may have been left incomplete."
                    ),
                    file=sys.stderr,
                )
                self._player.abort()
            else:
                elapsed = time.monotonic() - started_at
                print(_("[VoxCPMSpeaker] ✓ Playback finished ({elapsed:.1f}s).").format(elapsed=elapsed))
        except Exception as exc:  # noqa: BLE001
            print(_("[VoxCPMSpeaker] error while speaking: {exc}").format(exc=exc), file=sys.stderr)
            self._player.abort()
            return
        finally:
            self._release_gpu_memory()
            self._log_vram("after")
        if self._stop_event.is_set():
            return
        if collected:
            self._post_process_collected(text, collected)

    def stop(self) -> None:
        self._stop_event.set()
        self._player.abort()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._player.close()

    def _configure_voice_seed(self) -> None:
        mode = self._config.voice_seed_mode
        if mode == "off":
            return
        if mode == "fixed":
            path = self._config.voice_seed_path
            text = self._config.voice_seed_text
            if not path or not text:
                raise ValueError("voice_seed_mode='fixed' requires voice_seed_path and voice_seed_text")
            if not Path(path).exists():
                raise FileNotFoundError(f"voice_seed_path does not exist: {path}")
            self._seed_path = path
            self._seed_text = text
            return
        # mode == "auto"
        cache_dir = self._resolve_auto_seed_cache_dir()
        self._auto_seed_cache_dir = cache_dir
        cached_wav = cache_dir / _AUTO_SEED_WAV_FILENAME
        cached_txt = cache_dir / _AUTO_SEED_TEXT_FILENAME
        if cached_wav.exists() and cached_txt.exists():
            try:
                self._seed_text = cached_txt.read_text(encoding="utf-8")
                self._seed_path = str(cached_wav)
                print(
                    _("[VoxCPMSpeaker] Auto-seed loaded from {cached_wav}").format(cached_wav=cached_wav),
                )
            except OSError as exc:
                print(
                    _("[VoxCPMSpeaker] ⚠ Failed to read auto-seed: {exc}").format(exc=exc),
                    file=sys.stderr,
                )

    def _resolve_auto_seed_cache_dir(self) -> Path:
        if self._config.voice_seed_cache_dir:
            return Path(self._config.voice_seed_cache_dir)
        return Path.home() / ".cache" / "voicemate"

    def _load_model(self) -> Any:  # noqa: ANN401 — VoxCPM model is dynamic
        self._maybe_suppress_progress_bar()
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
        print(_("[VoxCPMSpeaker] Loading VoxCPM2 (first run downloads weights)..."))
        model = VoxCPM.from_pretrained("openbmb/VoxCPM2", **kwargs)
        print(_("[VoxCPMSpeaker] Model ready."))
        return model

    def _maybe_suppress_progress_bar(self) -> None:
        if self._config.show_progress:
            return
        try:
            import voxcpm.model.voxcpm2 as _vox2
        except ImportError:
            return

        def _identity(iterable: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            return iterable

        _vox2.tqdm = _identity  # type: ignore[assignment]

    def _warn_if_torch_lacks_cuda(self) -> None:
        try:
            import torch
        except ImportError:
            return
        # On ROCm, torch reports CUDA as available (HIP masquerades as cuda),
        # so this covers both accelerated NVIDIA and AMD.
        if torch.cuda.is_available():
            return
        print(
            _(
                "[VoxCPMSpeaker] ⚠ PyTorch without GPU acceleration — VoxCPM will run on CPU "
                "(synthesis ~50-100× slower than GPU)."
            ),
            file=sys.stderr,
        )
        if self._config.gpu_vendor == "amd":
            print(
                _(
                    "[VoxCPMSpeaker]   For the AMD GPU, install torch ROCm "
                    "(Adrenalin driver >= 26.2.2). Run: make configure"
                ),
                file=sys.stderr,
            )
            print(
                _(
                    "[VoxCPMSpeaker]   Wheels: https://repo.radeon.com/rocm/windows/ "
                    "(torch/torchaudio +rocm, cp312, win_amd64)"
                ),
                file=sys.stderr,
            )
        else:
            print(
                _("[VoxCPMSpeaker]   For the NVIDIA GPU, reinstall PyTorch with a CUDA build:"),
                file=sys.stderr,
            )
            print(
                _(
                    "[VoxCPMSpeaker]   poetry run pip install --upgrade --index-url "
                    "https://download.pytorch.org/whl/cu128 torch torchaudio"
                ),
                file=sys.stderr,
            )

    def _speak_streaming(self, text: str, collected: list[NDArray[np.float32]]) -> None:
        self._player.start(self._sample_rate)
        chunks = self._model.generate_streaming(**self._generate_kwargs(text))
        try:
            for chunk in chunks:
                if self._stop_event.is_set():
                    break
                arr = self._as_float32(chunk)
                collected.append(arr)
                self._player.feed(arr)
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # noqa: BLE001
                    print(
                        _("[VoxCPMSpeaker] ⚠ failed to close generator: {exc}").format(exc=exc),
                        file=sys.stderr,
                    )

    def _speak_oneshot(self, text: str, collected: list[NDArray[np.float32]]) -> None:
        self._player.start(self._sample_rate)
        wav = self._model.generate(**self._generate_kwargs(text))
        arr = self._as_float32(wav)
        collected.append(arr)
        if not self._stop_event.is_set():
            self._player.feed(arr)

    def _has_seed(self) -> bool:
        return self._seed_path is not None and self._seed_text is not None

    def _generate_kwargs(self, text: str) -> dict[str, Any]:
        """Build the kwargs for `generate`/`generate_streaming`.

        No seed → voice design mode: the description goes in parentheses at the
        start of the text and the model interprets it as an instruction (not
        spoken).

        With a seed (auto-loaded or fixed) → cloning mode: the voice is dictated
        by `prompt_wav_path` and `text` must be **only the sentence to be
        synthesized** — any extra text (including the parenthetical description)
        would be read aloud as content.
        """
        has_seed = self._has_seed()
        if has_seed:
            synthesis_text = text
        else:
            synthesis_text = f"({self._config.voice_description}) {text}"
        kwargs: dict[str, Any] = {
            "text": synthesis_text,
            "cfg_value": self._config.cfg_value,
            "inference_timesteps": self._config.inference_timesteps,
            "normalize": self._config.normalize,
        }
        if has_seed:
            kwargs["prompt_wav_path"] = self._seed_path
            kwargs["prompt_text"] = self._seed_text
        return kwargs

    def _post_process_collected(
        self,
        text: str,
        collected: list[NDArray[np.float32]],
    ) -> None:
        if self._config.voice_seed_mode == "auto" and self._seed_path is None:
            self._persist_auto_seed(text, collected)
        if self._config.save_audio_dir:
            self._save_audio(collected)

    def _persist_auto_seed(
        self,
        text: str,
        collected: list[NDArray[np.float32]],
    ) -> None:
        if self._auto_seed_cache_dir is None:
            return
        try:
            import soundfile as sf
        except ImportError:
            print(
                _("[VoxCPMSpeaker] soundfile not installed — could not save auto-seed."),
                file=sys.stderr,
            )
            return
        try:
            self._auto_seed_cache_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self._auto_seed_cache_dir / _AUTO_SEED_WAV_FILENAME
            txt_path = self._auto_seed_cache_dir / _AUTO_SEED_TEXT_FILENAME
            full = np.concatenate(collected)
            sf.write(str(wav_path), full, self._sample_rate)
            txt_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(_("[VoxCPMSpeaker] ⚠ Failed to save auto-seed: {exc}").format(exc=exc), file=sys.stderr)
            return
        self._seed_path = str(wav_path)
        self._seed_text = text
        print(_("[VoxCPMSpeaker] Auto-seed saved to {wav_path}").format(wav_path=wav_path))

    def _release_gpu_memory(self) -> None:
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _log_vram(self, label: str) -> None:
        if not self._config.debug_vram:
            return
        try:
            import torch
        except ImportError:
            return
        if not torch.cuda.is_available():
            return
        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        print(
            _("[VoxCPMSpeaker] VRAM ({label}): alloc={allocated:.2f}GB, reserved={reserved:.2f}GB").format(
                label=label, allocated=allocated, reserved=reserved
            )
        )

    @staticmethod
    def _as_float32(audio: Any) -> NDArray[np.float32]:  # noqa: ANN401 — chunk comes from the SDK
        arr = np.asarray(audio, dtype=np.float32)
        if arr.ndim > 1:
            arr = arr.reshape(-1)
        return arr

    def _save_audio(self, chunks: list[NDArray[np.float32]]) -> None:
        try:
            import soundfile as sf
        except ImportError:
            print(
                _("[VoxCPMSpeaker] soundfile not installed — could not save audio."),
                file=sys.stderr,
            )
            return
        directory = Path(self._config.save_audio_dir or ".")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"voxcpm_{int(time.time() * 1000)}.wav"
        full = np.concatenate(chunks)
        sf.write(str(path), full, self._sample_rate)
        print(_("[VoxCPMSpeaker] Audio saved to {path}").format(path=path))
