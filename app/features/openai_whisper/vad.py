"""VAD (silero) for openai-whisper: removes silence before transcribing.

openai-whisper has no built-in VAD/chunking (unlike main's faster-whisper), so
long audio — with pauses, breathing, and silence — is transcribed in full,
wasting GPU time for nothing. This helper uses silero-VAD (MIT) to extract only
the speech segments and concatenate them, reducing the effective duration.

Degrades safely: if `silero-vad` isn't installed, if no speech is detected, or
if anything fails, it returns the original audio intact.
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
from numpy.typing import NDArray

from app.i18n import _

_vad_model: Any = None
_unavailable = False


def trim_to_speech(audio: NDArray[np.float32], sample_rate: int = 16000) -> NDArray[np.float32]:
    """Return only the speech segments (silence removed). Original audio on fallback."""
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
    except Exception as exc:  # noqa: BLE001 — VAD is an optimization; never breaks transcription
        print(_("[VoiceMate] ⚠ VAD failed (using full audio): {exc}").format(exc=exc), file=sys.stderr)
        return audio
