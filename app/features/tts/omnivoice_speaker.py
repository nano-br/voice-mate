"""OmniVoice-based speaker (k2-fsa) — lightweight multilingual TTS (0.6B, 24 kHz).

Replaces VoxCPM2 as the default engine: lighter (~4 GB bf16) and fast. The API is
`OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=..., dtype=...)` +
`model.generate(text=..., ref_audio=..., ref_text=...)`, which returns a list of
`np.ndarray` at 24 kHz.

OmniVoice has no native streaming: the realtime granularity comes from the
handler, which calls `speak()` **sentence by sentence** (see `claude.chat_handler`).
Each `speak()` generates the audio for one sentence and plays it through the
`AudioPlayer` (queue), with instant cancellation via `stop()`.

Voice consistency reuses VoxCPM's same scheme (`voice_seed_mode`):
  - "auto": the first utterance becomes the reference (WAV+text) and is cloned
    in the following ones.
  - "fixed": uses an external user WAV as the reference.
  - "off": each turn uses the model's default voice.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.core.rocm_env import configure_rocm_env
from app.features.tts.audio_player import AudioSink, create_audio_player
from app.i18n import _

# OmniVoice-specific names so they don't collide with the VoxCPM seed (16 kHz) —
# OmniVoice operates at 24 kHz and uses a different reference model.
_AUTO_SEED_WAV_FILENAME = "voice_seed_omnivoice.wav"
_AUTO_SEED_TEXT_FILENAME = "voice_seed_omnivoice.txt"
_MODEL_ID = "k2-fsa/OmniVoice"
_SAMPLE_RATE = 24000
_FADE_MS = 6  # short fade-in/out at each sentence's edges — kills discontinuity clicks

# Maps the config code (derived output_lang) → OmniVoice language name.
# "auto" (or unknown) → don't pass language (the model detects it from the text).
_OMNI_LANG_NAMES = {
    "pt": "Portuguese",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
}


class OmniVoiceSpeaker:
    """OmniVoice speaker — lazy load, one-shot generation per sentence, streaming via AudioPlayer."""

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._player: AudioSink = create_audio_player()
        self._stop_event = threading.Event()
        self._load_lock = threading.Lock()  # serializes the load (warmup vs 1st utterance)
        self._closed = False
        self._load_failed = False
        self._seed_path: str | None = None
        self._seed_text: str | None = None
        self._auto_seed_cache_dir: Path | None = None
        self._configure_voice_seed()
        # ROCm optimizations (TunableOp + MIOpen FAST) need the env vars set
        # BEFORE torch imports — hence here in __init__, not in _load_model.
        self._configure_rocm_env()
        # Lazy load: the model (~4 GB bf16) only loads on the FIRST utterance —
        # clipboard-only sessions don't pay the VRAM/time cost.
        self._model: Any = None
        self._sample_rate = _SAMPLE_RATE

    def is_active(self) -> bool:
        return not self._closed and not self._load_failed

    def speak(self, text: str) -> None:
        """Synthesize ONE sentence and enqueue it on the persistent player (doesn't block to the end).

        Doesn't close/reopen the stream nor drain per sentence — the player keeps
        playing while the next sentence is generated (pipeline). The handler calls
        `wait_done()` at the end of the turn.
        """
        if self._closed or not text.strip():
            return
        if not self._ensure_model():
            return
        self._stop_event.clear()
        try:
            self._player.ensure_started(self._sample_rate)
            audio = self._model.generate(**self._generate_kwargs(text))
            arr = self._finalize_audio(self._as_float32(audio))
            if self._stop_event.is_set() or arr.size == 0:
                return
            self._player.feed(arr)
            self._maybe_persist_auto_seed(text, arr)
        except Exception as exc:  # noqa: BLE001
            print(_("[OmniVoiceSpeaker] error while speaking: {exc}").format(exc=exc), file=sys.stderr)
            self._player.abort()

    def wait_done(self, timeout: float | None = None) -> bool:
        """Wait for the audio queue to empty (end of turn) without closing the stream."""
        limit = timeout if timeout is not None else self._config.drain_timeout_seconds
        ok = self._player.drain(timeout=limit)
        if not ok:
            print(
                _("[OmniVoiceSpeaker] ⚠ AudioPlayer drain timed out — audio may have been left incomplete."),
                file=sys.stderr,
            )
            self._player.abort()
        self._release_gpu_memory()
        self._log_vram("end of turn")
        return ok

    def warmup(self) -> None:
        """Load the model and tune the kernels (TunableOp) outside the 1st real turn.

        Called in the background at startup (see main) — the 1st synthesis pays the
        tuning of the GEMMs on RDNA4 (seconds, once per process; the result is
        persisted in ~/.cache/voicemate). Without this, that cost would land on the
        1st spoken sentence of the conversation. Uses generate() directly (not
        speak()) so it does NOT become a seed.
        """
        if self._closed or self._load_failed:
            return
        if not self._ensure_model():
            return
        try:
            print(_("[OmniVoiceSpeaker] Warmup (tuning ROCm kernels; only the 1st time is slow)..."))
            self._model.generate(**self._generate_kwargs("Olá."))
            print(_("[OmniVoiceSpeaker] Warmup done — synthesis ready (realtime)."))
        except Exception as exc:  # noqa: BLE001 — warmup is an optimization, never breaks speech
            print(_("[OmniVoiceSpeaker] ⚠ warmup failed (continuing): {exc}").format(exc=exc), file=sys.stderr)
        finally:
            self._release_gpu_memory()

    def stop(self) -> None:
        self._stop_event.set()
        self._player.abort()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._player.close()

    # ── model ─────────────────────────────────────────────────────────────────

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        with self._load_lock:  # warmup (background) and the 1st utterance can compete
            if self._model is not None:
                return True
            if self._load_failed:
                return False
            try:
                self._model = self._load_model()
                return True
            except Exception as exc:  # noqa: BLE001
                print(_("[OmniVoiceSpeaker] ⚠ Failed to load OmniVoice: {exc}").format(exc=exc), file=sys.stderr)
                self._load_failed = True
                return False

    def _load_model(self) -> Any:  # noqa: ANN401 — OmniVoice model is dynamic
        import torch
        from omnivoice import OmniVoice

        self._warn_if_torch_lacks_gpu(torch)
        if getattr(torch.version, "hip", None):
            print(
                _(
                    "[OmniVoiceSpeaker] ROCm — TunableOp enabled: each new sentence length gets "
                    "a small tune (~1-2s) the 1st time; after that it is realtime (RTF ~0.7)."
                )
            )
        device_map, dtype = self._resolve_device_and_dtype(torch)
        print(
            _("[OmniVoiceSpeaker] Loading OmniVoice ({device_map}, first run downloads weights)...").format(
                device_map=device_map
            )
        )
        model = OmniVoice.from_pretrained(_MODEL_ID, device_map=device_map, dtype=dtype)
        print(_("[OmniVoiceSpeaker] Model ready."))
        return model

    def _configure_rocm_env(self) -> None:
        """Configure ROCm (MIOpen FAST + TunableOp) — key to realtime on AMD.

        Delegates to the shared function (the same one used by STT at boot); here
        it serves the isolated test path and as a safety net in case the speaker is
        created outside `main()`. Must run BEFORE torch imports (hence __init__).
        """
        configure_rocm_env(self._config.gpu_vendor, self._resolve_auto_seed_cache_dir())

    def _resolve_device_and_dtype(self, torch: Any) -> tuple[str, Any]:  # noqa: ANN401
        """Map config.device → OmniVoice device_map + dtype (bf16 on GPU)."""
        configured = self._config.device
        if configured == "auto":
            device_map = "cuda:0" if torch.cuda.is_available() else "cpu"
        elif configured == "cuda":
            device_map = "cuda:0"
        else:
            device_map = configured  # "cpu" | "mps"
        # bf16 = best quality/speed/VRAM trade-off on GPU; fp32 on CPU.
        accelerated = device_map.startswith("cuda") or device_map == "mps"
        dtype = torch.bfloat16 if accelerated else torch.float32
        return device_map, dtype

    def _warn_if_torch_lacks_gpu(self, torch: Any) -> None:  # noqa: ANN401
        # On ROCm, torch reports CUDA as available (HIP masquerades as cuda).
        if torch.cuda.is_available() or self._config.device == "cpu":
            return
        print(
            _(
                "[OmniVoiceSpeaker] ⚠ PyTorch without GPU acceleration — OmniVoice will run on CPU "
                "(much slower synthesis). Run `make configure` to install the torch for your GPU."
            ),
            file=sys.stderr,
        )

    # ── generation / voice ──────────────────────────────────────────────────────

    def _generate_kwargs(self, text: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"text": text}
        language = _OMNI_LANG_NAMES.get(self._config.language)
        if language:  # "auto"/unknown → omit (OmniVoice detects it from the text)
            kwargs["language"] = language
        if self._has_seed():  # "fixed"/"auto"(with seed) → reference-based cloning
            kwargs["ref_audio"] = self._seed_path
            kwargs["ref_text"] = self._seed_text
        # default "off": plain (no ref) — no cloning. We do NOT use voice-design
        # (`instruct`) because its multiprocessing pool hangs on Windows/ROCm.
        return kwargs

    def _finalize_audio(self, arr: NDArray[np.float32]) -> NDArray[np.float32]:
        """Clip to [-1,1] and apply a short fade-in/out — removes edge clicks."""
        if arr.size == 0:
            return arr
        np.clip(arr, -1.0, 1.0, out=arr)
        n = min(int(_SAMPLE_RATE * _FADE_MS / 1000), arr.size // 2)
        if n > 0:
            ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
            arr[:n] *= ramp
            arr[-n:] *= ramp[::-1]
        return arr

    def _has_seed(self) -> bool:
        return self._seed_path is not None and self._seed_text is not None

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
                print(_("[OmniVoiceSpeaker] Auto-seed loaded from {cached_wav}").format(cached_wav=cached_wav))
            except OSError as exc:
                print(_("[OmniVoiceSpeaker] ⚠ Failed to read auto-seed: {exc}").format(exc=exc), file=sys.stderr)

    def _resolve_auto_seed_cache_dir(self) -> Path:
        if self._config.voice_seed_cache_dir:
            return Path(self._config.voice_seed_cache_dir)
        return Path.home() / ".cache" / "voicemate"

    def _maybe_persist_auto_seed(self, text: str, arr: NDArray[np.float32]) -> None:
        # Only in the opt-in cloning modes; the default ("off"/voice-design) never reaches here.
        if self._config.voice_seed_mode == "auto" and self._seed_path is None:
            self._persist_auto_seed(text, [arr])
        if self._config.save_audio_dir:
            self._save_audio([arr])

    def _persist_auto_seed(self, text: str, collected: list[NDArray[np.float32]]) -> None:
        if self._auto_seed_cache_dir is None:
            return
        try:
            import soundfile as sf
        except ImportError:
            print(_("[OmniVoiceSpeaker] soundfile not installed — could not save auto-seed."), file=sys.stderr)
            return
        try:
            self._auto_seed_cache_dir.mkdir(parents=True, exist_ok=True)
            wav_path = self._auto_seed_cache_dir / _AUTO_SEED_WAV_FILENAME
            txt_path = self._auto_seed_cache_dir / _AUTO_SEED_TEXT_FILENAME
            sf.write(str(wav_path), np.concatenate(collected), self._sample_rate)
            txt_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(_("[OmniVoiceSpeaker] ⚠ Failed to save auto-seed: {exc}").format(exc=exc), file=sys.stderr)
            return
        self._seed_path = str(wav_path)
        self._seed_text = text
        print(_("[OmniVoiceSpeaker] Auto-seed saved to {wav_path}").format(wav_path=wav_path))

    def _save_audio(self, chunks: list[NDArray[np.float32]]) -> None:
        try:
            import soundfile as sf
        except ImportError:
            print(_("[OmniVoiceSpeaker] soundfile not installed — could not save audio."), file=sys.stderr)
            return
        directory = Path(self._config.save_audio_dir or ".")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"omnivoice_{int(time.time() * 1000)}.wav"
        sf.write(str(path), np.concatenate(chunks), self._sample_rate)
        print(_("[OmniVoiceSpeaker] Audio saved to {path}").format(path=path))

    # ── utilities ───────────────────────────────────────────────────────────────

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
            _("[OmniVoiceSpeaker] VRAM ({label}): alloc={allocated:.2f}GB, reserved={reserved:.2f}GB").format(
                label=label, allocated=allocated, reserved=reserved
            )
        )

    @staticmethod
    def _as_float32(audio: Any) -> NDArray[np.float32]:  # noqa: ANN401 — SDK output
        """Normalize the OmniVoice output (list of np.ndarray at 24 kHz) into 1 mono vector."""
        items = audio if isinstance(audio, (list, tuple)) else [audio]
        arrays = [OmniVoiceSpeaker._coerce(a) for a in items]
        arrays = [a for a in arrays if a.size > 0]
        if not arrays:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(arrays)

    @staticmethod
    def _coerce(a: Any) -> NDArray[np.float32]:  # noqa: ANN401
        if hasattr(a, "detach"):  # torch tensor → numpy
            a = a.detach().to("cpu").numpy()
        arr = np.asarray(a, dtype=np.float32)
        return arr.reshape(-1) if arr.ndim > 1 else arr
