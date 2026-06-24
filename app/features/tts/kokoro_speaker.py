"""Speaker baseado em Kokoro (hexgrad, Apache-2.0) — TTS leve, 24 kHz.

Kokoro é um modelo pequeno (~82M, NÃO-difusão): usa pouquíssima GPU e roda em
realtime (40-90×), o que evita saturar a GPU na síntese — diferente do OmniVoice
(difusão, compute-bound). Em troca, NÃO clona voz: usa vozes fixas (ex.: PT-BR
`pf_dora` feminina, `pm_alex`/`pm_santa` masculinas).

API: `KPipeline(lang_code='p')` (p = português-BR) e `pipeline(text, voice=...)`
que é um GERADOR — produz `(gs, ps, audio)` por trecho. Aproveitamos esse
streaming nativo alimentando cada `audio` (24 kHz float32) direto no `AudioPlayer`
(fila persistente, mesmos params anti-underrun do WSLg).

Requisito de sistema: `espeak-ng` (G2P do PT-BR). Sem ele, o load falha e o
speaker degrada para inativo (o handler cai no beep).
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

_SAMPLE_RATE = 24000

# Código do config (output_lang derivado) → lang_code do Kokoro.
# "auto"/desconhecido → 'a' (inglês americano, default seguro do Kokoro).
_KOKORO_LANG_CODES = {
    "pt": "p",  # português (Brasil)
    "en": "a",  # inglês americano
    "es": "e",
    "fr": "f",
    "it": "i",
    "ja": "j",
    "zh": "z",
}


class KokoroSpeaker:
    """Speaker Kokoro — lazy load, streaming nativo por trecho via AudioPlayer."""

    def __init__(self, config: TTSConfig) -> None:
        self._config = config
        self._player: AudioSink = create_audio_player()
        self._stop_event = threading.Event()
        self._load_lock = threading.Lock()  # serializa o load (warmup vs 1ª fala)
        self._closed = False
        self._load_failed = False
        # Env de ROCm (MIOpen FAST + TunableOp) antes de o torch importar.
        configure_rocm_env(self._config.gpu_vendor)
        # Lazy load: o pipeline só carrega na PRIMEIRA fala (ou no warmup).
        self._pipeline: Any = None
        self._sample_rate = _SAMPLE_RATE

    def is_active(self) -> bool:
        return not self._closed and not self._load_failed

    def speak(self, text: str) -> None:
        """Sintetiza o texto e enfileira cada trecho no player (não bloqueia até o fim)."""
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
            print(f"[KokoroSpeaker] erro ao falar: {exc}", file=sys.stderr)
            self._player.abort()

    def wait_done(self, timeout: float | None = None) -> bool:
        """Espera a fila de áudio esvaziar (fim do turno) sem fechar o stream."""
        limit = timeout if timeout is not None else self._config.drain_timeout_seconds
        ok = self._player.drain(timeout=limit)
        if not ok:
            print(
                "[KokoroSpeaker] ⚠ drain do AudioPlayer estourou — áudio pode ter ficado incompleto.", file=sys.stderr
            )
            self._player.abort()
        return ok

    def warmup(self) -> None:
        """Carrega o pipeline e sintetiza uma frase curta fora do 1º turno real."""
        if self._closed or self._load_failed:
            return
        if not self._ensure_pipeline():
            return
        try:
            print("[KokoroSpeaker] Warmup...")
            for _chunk in self._synthesize("Olá."):  # consome o gerador (sem tocar)
                pass
            print("[KokoroSpeaker] Warmup concluído — síntese pronta.")
        except Exception as exc:  # noqa: BLE001 — warmup é otimização, nunca quebra a fala
            print(f"[KokoroSpeaker] ⚠ warmup falhou (seguindo): {exc}", file=sys.stderr)

    def stop(self) -> None:
        self._stop_event.set()
        self._player.abort()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        self._player.close()

    # ── modelo ────────────────────────────────────────────────────────────────

    def _ensure_pipeline(self) -> bool:
        if self._pipeline is not None:
            return True
        with self._load_lock:  # warmup (background) e a 1ª fala podem competir
            if self._pipeline is not None:
                return True
            if self._load_failed:
                return False
            try:
                self._pipeline = self._load_pipeline()
                return True
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[KokoroSpeaker] ⚠ Falha ao carregar Kokoro: {exc}\n"
                    "[KokoroSpeaker]   PT-BR precisa do espeak-ng: sudo apt install -y espeak-ng",
                    file=sys.stderr,
                )
                self._load_failed = True
                return False

    def _load_pipeline(self) -> Any:  # noqa: ANN401 — KPipeline é dinâmico
        from kokoro import KPipeline

        device = self._resolve_device()
        lang_code = _KOKORO_LANG_CODES.get(self._config.language, "a")
        print(f"[KokoroSpeaker] Carregando Kokoro (lang={lang_code}, {device}, 1ª execução baixa pesos)...")
        pipeline = KPipeline(lang_code=lang_code, device=device)
        print("[KokoroSpeaker] Modelo pronto.")
        return pipeline

    def _resolve_device(self) -> str:
        configured = self._config.device
        if configured == "cpu":
            return "cpu"
        if configured in ("cuda", "mps"):
            return "cuda:0" if configured == "cuda" else "mps"
        # "auto" → CPU DE PROPÓSITO. Kokoro é realtime na CPU (RTF ~0.24 medido na
        # RX 9070 XT) e assim NÃO disputa a GPU com a transcrição — a causa do
        # chiado por contenção. Bônus: na AMD/ROCm os kernels do Kokoro são lentos
        # (RTF ~1.8 na GPU), então CPU é melhor nos dois sentidos. Force com
        # --tts-device cuda se quiser a GPU.
        return "cpu"

    def _synthesize(self, text: str) -> Any:  # noqa: ANN401 — gerador de chunks
        """Itera o gerador do Kokoro, devolvendo cada trecho como float32 mono."""
        for result in self._pipeline(text, voice=self._config.kokoro_voice):
            audio = result[2] if isinstance(result, (list, tuple)) else getattr(result, "audio", result)
            yield self._coerce(audio)

    @staticmethod
    def _coerce(a: Any) -> NDArray[np.float32]:  # noqa: ANN401
        if hasattr(a, "detach"):  # tensor torch → numpy
            a = a.detach().to("cpu").numpy()
        arr = np.asarray(a, dtype=np.float32)
        return arr.reshape(-1) if arr.ndim > 1 else arr
