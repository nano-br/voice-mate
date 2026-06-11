import numpy as np
from faster_whisper import WhisperModel
from numpy.typing import NDArray


class FasterWhisperBackend:
    """Transcreve áudio usando faster-whisper (CTranslate2 backend).

    Cobre NVIDIA (device="cuda", int8_float16) e CPU (int8). Na AMD só funciona
    quando o CTranslate2 instalado é o fork ROCm (o HIP se reporta como "cuda");
    a factory `cli.wiring.build_transcriber` decide isso via `ct2_rocm_ok` e cai
    para whisper.cpp/openai-whisper quando não. Satisfaz o Protocol
    `core.transcription_backend.TranscriptionBackend`.

    `language=None` deixa o Whisper detectar o idioma por fala; fixar (ex.: "pt")
    dá estabilidade — evita o detector classificar uma fala curta no idioma errado
    (code-switching de termos estrangeiros continua funcionando).
    """

    def __init__(self, model_size: str, use_cpu: bool, beam_size: int, language: str | None = None) -> None:
        device = "cpu" if use_cpu else "cuda"
        compute_type = "int8" if use_cpu else "int8_float16"
        print(f"[VoiceMate] Carregando Whisper '{model_size}' em {device.upper()} ({compute_type})...")
        print("[VoiceMate] (primeira execução baixa o modelo automaticamente)")
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        self._beam_size = beam_size
        self._language = language
        print("[VoiceMate] Modelo pronto.")

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        """Retorna o texto transcrito, ou string vazia se nenhuma fala detectada."""
        segments, _ = self._model.transcribe(audio, beam_size=self._beam_size, language=self._language)
        return " ".join(seg.text for seg in segments).strip()


# Back-compat: código/testes que importam `Transcriber` continuam funcionando.
Transcriber = FasterWhisperBackend
