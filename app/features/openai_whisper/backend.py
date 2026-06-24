from __future__ import annotations

import sys
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config

_WHISPER_SAMPLE_RATE = 16000
# Acima disso, vale rodar o VAD p/ cortar silêncio (áudio longo). Abaixo, o
# áudio já transcreve em sub-segundo e o VAD só adicionaria latência.
_VAD_MIN_SAMPLES = 20 * _WHISPER_SAMPLE_RATE


class OpenAIWhisperBackend:
    """Transcreve com openai-whisper sobre torch (CUDA/HIP-ROCm ou CPU).

    Satisfaz o Protocol `core.transcription_backend.TranscriptionBackend`.
    Se a GPU for pedida mas o torch não tiver CUDA/ROCm disponível, levanta
    RuntimeError — a factory `cli.wiring.build_transcriber` captura e cai para
    o faster-whisper em CPU (mais rápido que openai-whisper em CPU).
    """

    def __init__(self, config: Config) -> None:
        import whisper  # openai-whisper

        device = self._resolve_device(config)
        self._beam_size = config.beam_size
        self._fp16 = device == "cuda"
        # Idioma fixado (≠"auto"): estabilidade + pula a detecção por fala
        # (mesma regra dos outros backends; code-switching continua funcionando).
        self._language: str | None = None if config.transcription_language == "auto" else config.transcription_language
        print(f"[VoiceMate] Carregando Whisper '{config.model_size}' (openai-whisper) em {device.upper()}...")
        print("[VoiceMate] (primeira execução baixa o modelo automaticamente)")
        self._model = whisper.load_model(config.model_size, device=device)
        print("[VoiceMate] Modelo pronto.")

    @staticmethod
    def _resolve_device(config: Config) -> str:
        if config.use_cpu or config.gpu_vendor == "cpu":
            return "cpu"
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("openai-whisper precisa de torch instalado.") from exc
        # Em ROCm o torch reporta CUDA disponível (HIP se disfarça de cuda).
        if not torch.cuda.is_available():
            raise RuntimeError(
                "Backend GPU pedido, mas torch.cuda.is_available() é False "
                "(driver ROCm/CUDA ausente ou torch CPU-only instalado)."
            )
        return "cuda"

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        # Áudio longo: corta o silêncio com VAD antes de transcrever (o
        # openai-whisper não tem VAD embutido como o faster-whisper). Áudio
        # curto (comandos do dia a dia) passa direto — já é sub-segundo.
        if audio.shape[0] > _VAD_MIN_SAMPLES:
            from app.features.openai_whisper.vad import trim_to_speech

            audio = trim_to_speech(audio, _WHISPER_SAMPLE_RATE)
        result: dict[str, Any] = self._model.transcribe(
            audio,
            beam_size=self._beam_size,
            fp16=self._fp16,
            language=self._language,
            # temperature=0.0 desliga a escada de fallback do Whisper
            # (0.0, 0.2…1.0): por padrão, quando um segmento de baixa confiança
            # "falha" nos limiares (comum em áudio de microfone real), ele
            # RE-DECODIFICA o mesmo trecho até 6×. Em áudio longo/ruidoso isso
            # multiplica os forward-passes e crava a GPU. Fixar em 0.0 mantém a
            # decodificação determinística e rápida.
            temperature=0.0,
            # condition_on_previous_text=False: evita o contexto crescer ao
            # longo de áudio longo contínuo (que desacelera cada janela de 30s)
            # e reduz o risco de loop de repetição. Prioriza velocidade.
            condition_on_previous_text=False,
            verbose=False,
        )
        text = result.get("text", "")
        return text.strip() if isinstance(text, str) else ""

    def warmup(self) -> None:
        """Transcreve 1s de silêncio p/ pagar a busca de kernels MIOpen no startup.

        Sem isto, a 1ª transcrição real paga o custo (dezenas de segundos na AMD),
        ainda por cima disputando GPU com o warmup do TTS. Best-effort: silencioso.
        """
        try:
            self.transcribe(np.zeros(16000, dtype=np.float32))
        except Exception as exc:  # noqa: BLE001 — warmup é best-effort
            print(f"[VoiceMate] ⚠ warmup do openai-whisper falhou (seguindo): {exc}", file=sys.stderr)
