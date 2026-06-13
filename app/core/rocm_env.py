"""Configuração de ambiente ROCm (MIOpen + TunableOp) — compartilhada STT/TTS.

Precisa rodar ANTES de o torch ser importado/inicializar o MIOpen, por isso é
chamada no topo de `main()` (e, por segurança/idempotência, no `__init__` do
OmniVoiceSpeaker para o caminho de teste isolado). Sem `MIOPEN_FIND_MODE=FAST`,
as convoluções (codec do TTS e encoder do Whisper) caem na busca exaustiva de
kernels — dezenas de segundos no 1º uso e warnings de workspace=0. `setdefault`
respeita qualquer override do usuário.
"""

from __future__ import annotations

import os
from pathlib import Path

_CACHE_DIR = Path.home() / ".cache" / "voicemate"


def configure_rocm_env(gpu_vendor: str, cache_dir: Path | None = None) -> None:
    """Seta as env vars de MIOpen/TunableOp para a AMD. No-op em outros vendors."""
    if gpu_vendor != "amd":
        return
    cache = cache_dir or _CACHE_DIR
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    # MIOpen — FAST usa heurística (zera a busca exaustiva de kernels e os
    # warnings de workspace=0); USER_DB_PATH persiste a find-db entre runs.
    os.environ.setdefault("MIOPEN_FIND_MODE", "FAST")
    os.environ.setdefault("MIOPEN_USER_DB_PATH", str(cache / "miopen"))
    # TunableOp — tuna os GEMMs; limites baixos evitam congelar em shapes novos.
    os.environ.setdefault("PYTORCH_TUNABLEOP_ENABLED", "1")
    os.environ.setdefault("PYTORCH_TUNABLEOP_FILENAME", str(cache / "tunableop.csv"))
    os.environ.setdefault("PYTORCH_TUNABLEOP_MAX_TUNING_DURATION_MS", "15")
    os.environ.setdefault("PYTORCH_TUNABLEOP_MAX_TUNING_ITERATIONS", "5")
