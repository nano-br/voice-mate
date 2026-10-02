"""ROCm environment setup (MIOpen + TunableOp) — shared by STT/TTS.

Must run BEFORE torch is imported/initializes MIOpen, so it is called at the top
of `main()` (and, for safety/idempotency, in OmniVoiceSpeaker's `__init__` for the
isolated test path). Without `MIOPEN_FIND_MODE=FAST`, the convolutions (TTS codec
and Whisper encoder) fall into the exhaustive kernel search — tens of seconds on
first use and workspace=0 warnings. `setdefault` respects any user override.
"""

from __future__ import annotations

import os
from pathlib import Path

_CACHE_DIR = Path.home() / ".cache" / "voicemate"


def configure_rocm_env(gpu_vendor: str, cache_dir: Path | None = None) -> None:
    """Set the MIOpen/TunableOp env vars for AMD. No-op on other vendors."""
    if gpu_vendor != "amd":
        return
    cache = cache_dir or _CACHE_DIR
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    # MIOpen — FAST uses a heuristic (eliminates the exhaustive kernel search and
    # the workspace=0 warnings); USER_DB_PATH persists the find-db across runs.
    os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
    os.environ.setdefault("MIOPEN_USER_DB_PATH", str(cache / "miopen"))
    # TunableOp — tunes the GEMMs; low limits avoid freezing on new shapes.
    os.environ.setdefault("PYTORCH_TUNABLEOP_ENABLED", "1")
    os.environ.setdefault("PYTORCH_TUNABLEOP_FILENAME", str(cache / "tunableop.csv"))
    os.environ.setdefault("PYTORCH_TUNABLEOP_MAX_TUNING_DURATION_MS", "15")
    os.environ.setdefault("PYTORCH_TUNABLEOP_MAX_TUNING_ITERATIONS", "5")
