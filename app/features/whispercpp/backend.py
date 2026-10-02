from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config
from app.i18n import _


def _write_wav_16k(audio: NDArray[np.float32], sample_rate: int, path: Path) -> None:
    """Write the float32 [-1,1] audio as mono PCM16 WAV (the format whisper.cpp reads).

    Uses only the stdlib `wave` — the backend doesn't depend on soundfile.
    """
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())


class WhisperCppBackend:
    """Transcribes via `whisper-cli.exe` (whisper.cpp + Vulkan) over a subprocess.

    Satisfies the `core.transcription_backend.TranscriptionBackend` Protocol.
    On each call: writes a temporary WAV, runs the CLI with `-otxt` (plain text
    output to a file — robust, without the `\\r` that corrupts `-nt`'s output),
    reads the `.txt`, and cleans up the temporaries.
    """

    def __init__(self, config: Config, exe: Path, model: Path) -> None:
        self._exe = exe
        self._model = model
        self._beam = config.beam_size
        self._sample_rate = config.sample_rate
        self._language = config.transcription_language
        print(
            _("[VoiceMate] Loading Whisper '{model_stem}' (whisper.cpp + Vulkan, language={language})...").format(
                model_stem=model.stem, language=self._language
            )
        )
        print(_("[VoiceMate] Model ready."))

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        fd, wav_name = tempfile.mkstemp(suffix=".wav", prefix="voicemate_wcpp_")
        os.close(fd)
        wav_path = Path(wav_name)
        out_prefix = wav_path.with_suffix("")  # whisper.cpp appends .txt to this prefix
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
                self._language,
                "-otxt",
                "-of",
                str(out_prefix),
                "-np",
            ]
            from app.features.whispercpp import find_vad_model

            vad_model = find_vad_model(self._model.parent)
            if vad_model is not None:
                cmd += ["--vad", "--vad-model", str(vad_model)]
            # Fixed encoding/errors: whisper-cli emits UTF-8 (progress bars,
            # multilingual text) that Windows' default cp1252 can't decode —
            # without this, the subprocess reader thread raises UnicodeDecodeError.
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
                raise RuntimeError(f"whisper-cli failed (code {proc.returncode}): {tail}")
            text = txt_path.read_text(encoding="utf-8") if txt_path.exists() else ""
            # Faithful concatenation: -otxt writes one segment per line, each
            # already with its leading space when applicable. Re-joining with " "
            # after a per-line strip() inserted a space inside words when a segment
            # broke in the middle of one ("pa lavra").
            return "".join(text.splitlines()).strip()
        finally:
            for leftover in (wav_path, txt_path):
                try:
                    leftover.unlink()
                except OSError:
                    pass
