"""Kokoro-based speaker (hexgrad, Apache-2.0) — lightweight TTS, 24 kHz.

Kokoro is a small model (~82M, NON-diffusion): uses very little GPU and runs
realtime (40-90×), which avoids saturating the GPU during synthesis — unlike
OmniVoice (diffusion, compute-bound). In exchange, it does NOT clone voices: it
uses fixed voices (e.g. PT-BR `pf_dora` female, `pm_alex`/`pm_santa` male).

API: `KPipeline(lang_code='p')` (p = Brazilian Portuguese) and
`pipeline(text, voice=...)` which is a GENERATOR — yields `(gs, ps, audio)` per
chunk. We leverage this native streaming by feeding each `audio` (24 kHz float32)
straight into the `AudioPlayer` (persistent queue, same WSLg anti-underrun params).

System requirement: `espeak-ng` (PT-BR G2P). Without it, the load fails and the
speaker degrades to inactive (the handler falls back to the beep).
"""

from __future__ import annotations

import sys
import threading
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.core.rocm_env import configure_rocm_env
from app.features.tts.audio_player import AudioSink, create_audio_player
from app.i18n import _

_SAMPLE_RATE = 24000

# Config code (derived output_lang) → Kokoro lang_code.
# "auto"/unknown → 'a' (American English, Kokoro's safe default).
_KOKORO_LANG_CODES = {
    "pt": "p",  # Portuguese (Brazil)
    "en": "a",  # American English
    "es": "e",
    "fr": "f",
    "it": "i",
    "ja": "j",
    "zh": "z",
}


class KokoroSpeaker:
    """Kokoro speaker — lazy load, native per-chunk streaming via AudioPlayer."""

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._player: AudioSink = create_audio_player()
        self._stop_event = threading.Event()
        self._load_lock = threading.Lock()  # serializes the load (warmup vs 1st utterance)
        self._closed = False
        self._load_failed = False
        # ROCm env (MIOpen FAST + TunableOp) before torch imports.
        configure_rocm_env(self._config.gpu_vendor)
        # Lazy load: the pipeline only loads on the FIRST utterance (or on warmup).
        self._pipeline: Any = None
        self._sample_rate = _SAMPLE_RATE

    def is_active(self) -> bool:
        return not self._closed and not self._load_failed

    def speak(self, text: str) -> None:
        """Synthesize the text and enqueue each chunk on the player (doesn't block to the end)."""
        if self._closed or not text.strip():
            return
        if not self._ensure_pipeline():
            return
        self._stop_event.clear()
        try:
            self._player.ensure_started(self._sample_rate)
            for chunk in self._synthesize(text):
                if self._stop_event.is_set():
                    return
                if chunk.size > 0:
                    self._player.feed(chunk)
        except Exception as exc:  # noqa: BLE001
            print(_("[KokoroSpeaker] error while speaking: {exc}").format(exc=exc), file=sys.stderr)
            self._player.abort()

    def wait_done(self, timeout: float | None = None) -> bool:
        """Wait for the audio queue to empty (end of turn) without closing the stream."""
        limit = timeout if timeout is not None else self._config.drain_timeout_seconds
        ok = self._player.drain(timeout=limit)
        if not ok:
            print(
                _("[KokoroSpeaker] ⚠ AudioPlayer drain timed out — audio may have been left incomplete."),
                file=sys.stderr,
            )
            self._player.abort()
        return ok

    def warmup(self) -> None:
        """Load the pipeline and synthesize a short sentence outside the 1st real turn."""
        if self._closed or self._load_failed:
            return
        if not self._ensure_pipeline():
            return
        try:
            print(_("[KokoroSpeaker] Warmup..."))
            for _chunk in self._synthesize("Olá."):  # consume the generator (without playing)
                pass
            print(_("[KokoroSpeaker] Warmup done — synthesis ready."))
        except Exception as exc:  # noqa: BLE001 — warmup is an optimization, never breaks speech
            print(_("[KokoroSpeaker] ⚠ warmup failed (continuing): {exc}").format(exc=exc), file=sys.stderr)

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

    def _ensure_pipeline(self) -> bool:
        if self._pipeline is not None:
            return True
        with self._load_lock:  # warmup (background) and the 1st utterance can compete
            if self._pipeline is not None:
                return True
            if self._load_failed:
                return False
            try:
                self._pipeline = self._load_pipeline()
                return True
            except Exception as exc:  # noqa: BLE001
                print(
                    _(
                        "[KokoroSpeaker] ⚠ Failed to load Kokoro: {exc}\n"
                        "[KokoroSpeaker]   PT-BR needs espeak-ng: sudo apt install -y espeak-ng"
                    ).format(exc=exc),
                    file=sys.stderr,
                )
                self._load_failed = True
                return False

    def _load_pipeline(self) -> Any:  # noqa: ANN401 — KPipeline is dynamic
        from kokoro import KPipeline

        device = self._resolve_device()
        lang_code = _KOKORO_LANG_CODES.get(self._config.language, "a")
        print(
            _("[KokoroSpeaker] Loading Kokoro (lang={lang_code}, {device}, first run downloads weights)...").format(
                lang_code=lang_code, device=device
            )
        )
        pipeline = KPipeline(lang_code=lang_code, device=device)
        print(_("[KokoroSpeaker] Model ready."))
        return pipeline

    def _resolve_device(self) -> str:
        configured = self._config.device
        if configured == "cpu":
            return "cpu"
        if configured in ("cuda", "mps"):
            return "cuda:0" if configured == "cuda" else "mps"
        # "auto" → CPU ON PURPOSE. Kokoro is realtime on CPU (RTF ~0.24 measured on
        # the RX 9070 XT) and so does NOT contend for the GPU with transcription —
        # the cause of the contention crackle. Bonus: on AMD/ROCm Kokoro's kernels
        # are slow (RTF ~1.8 on GPU), so CPU is better both ways. Force it with
        # --tts-device cuda if you want the GPU.
        return "cpu"

    def _synthesize(self, text: str) -> Any:  # noqa: ANN401 — generator of chunks
        """Iterate Kokoro's generator, yielding each chunk as mono float32."""
        for result in self._pipeline(text, voice=self._config.kokoro_voice):
            audio = result[2] if isinstance(result, (list, tuple)) else getattr(result, "audio", result)
            yield self._coerce(audio)

    @staticmethod
    def _coerce(a: Any) -> NDArray[np.float32]:  # noqa: ANN401
        if hasattr(a, "detach"):  # torch tensor → numpy
            a = a.detach().to("cpu").numpy()
        arr = np.asarray(a, dtype=np.float32)
        return arr.reshape(-1) if arr.ndim > 1 else arr
