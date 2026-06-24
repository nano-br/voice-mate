"""VAD (silero) para o openai-whisper: remove silêncio antes de transcrever.

O openai-whisper não tem VAD/chunking embutido (ao contrário do faster-whisper
da main), então áudio longo — com pausas, respiração e silêncio — é transcrito
inteiro, gastando tempo de GPU à toa. Este helper usa o silero-VAD (MIT) para
extrair só os trechos de fala e concatená-los, reduzindo a duração efetiva.

Degrada com segurança: se o `silero-vad` não estiver instalado, se nada de fala
for detectado, ou se algo falhar, devolve o áudio original intacto.
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
from numpy.typing import NDArray

_vad_model: Any = None
_unavailable = False


def trim_to_speech(audio: NDArray[np.float32], sample_rate: int = 16000) -> NDArray[np.float32]:
    """Devolve só os trechos de fala (silêncio removido). Áudio original em fallback."""
    global _vad_model, _unavailable
    if _unavailable:
        return audio
    try:
        import torch
        from silero_vad import collect_chunks, get_speech_timestamps, load_silero_vad
    except ImportError:
        _unavailable = True
        return audio
    try:
        if _vad_model is None:
            _vad_model = load_silero_vad()
        tensor = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))
        timestamps = get_speech_timestamps(tensor, _vad_model, sampling_rate=sample_rate)
        if not timestamps:
            return audio
        speech = collect_chunks(timestamps, tensor)
        return np.asarray(speech.numpy(), dtype=np.float32)
    except Exception as exc:  # noqa: BLE001 — VAD é otimização; nunca quebra a transcrição
        print(f"[VoiceMate] ⚠ VAD falhou (usando áudio completo): {exc}", file=sys.stderr)
        return audio
