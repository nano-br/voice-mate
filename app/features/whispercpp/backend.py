from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config


def _write_wav_16k(audio: NDArray[np.float32], sample_rate: int, path: Path) -> None:
    """Grava o áudio float32 [-1,1] como WAV PCM16 mono (formato que o whisper.cpp lê).

    Usa só a stdlib `wave` — o backend não depende de soundfile.
    """
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())


class WhisperCppBackend:
    """Transcreve via `whisper-cli.exe` (whisper.cpp + Vulkan) por subprocess.

    Satisfaz o Protocol `core.transcription_backend.TranscriptionBackend`.
    A cada chamada: grava um WAV temporário, roda o CLI com `-otxt` (saída de
    texto puro num arquivo — robusto, sem o `\\r` que corrompe a saída de `-nt`),
    lê o `.txt` e limpa os temporários.
    """

    def __init__(self, config: Config, exe: Path, model: Path) -> None:
        self._exe = exe
        self._model = model
        self._beam = config.beam_size
        self._sample_rate = config.sample_rate
        print(f"[VoiceMate] Carregando Whisper '{model.stem}' (whisper.cpp + Vulkan)...")
        print("[VoiceMate] Modelo pronto.")

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        fd, wav_name = tempfile.mkstemp(suffix=".wav", prefix="voicemate_wcpp_")
        os.close(fd)
        wav_path = Path(wav_name)
        out_prefix = wav_path.with_suffix("")  # whisper.cpp anexa .txt a este prefixo
        txt_path = Path(f"{out_prefix}.txt")
        try:
            _write_wav_16k(audio, self._sample_rate, wav_path)
            cmd = [
                str(self._exe),
                "-m",
                str(self._model),
                "-f",
                str(wav_path),
                "-bs",
                str(self._beam),
                "-l",
                "auto",
                "-otxt",
                "-of",
                str(out_prefix),
                "-np",
            ]
            # encoding/errors fixos: o whisper-cli emite UTF-8 (barras de progresso,
            # texto multilíngue) que o cp1252 padrão do Windows não decodifica —
            # sem isto, o thread leitor do subprocess levanta UnicodeDecodeError.
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self._exe.parent),
            )
            if proc.returncode != 0:
                tail = (proc.stderr or "")[-400:]
                raise RuntimeError(f"whisper-cli falhou (código {proc.returncode}): {tail}")
            text = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
            return " ".join(line.strip() for line in text.splitlines() if line.strip())
        finally:
            for leftover in (wav_path, txt_path):
                try:
                    leftover.unlink()
                except OSError:
                    pass
