"""Transcription by an external provider over HTTP (docs/stt-provider-protocol.md).

Needs no extra package: the recording is sent to a service the person runs, and VoiceMate knows
nothing about what is behind it. `ExternalTranscriber` falls back to the built-in engine while the
provider cannot be used (`ExternalSttConfig.fallback`).
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Config
from app.core.transcription_backend import TranscriptionBackend
from app.features.stt_external.transcriber import ExternalTranscriber

__all__ = ["ExternalTranscriber", "build_backend"]


def build_backend(config: Config, builtin: Callable[[], TranscriptionBackend]) -> ExternalTranscriber:
    """The provider's transcriber; `builtin` builds the fallback engine when it is first needed."""
    return ExternalTranscriber(config, builtin=builtin)
