"""Garante stdout/stderr em UTF-8 (o app imprime emoji/→/acentos o tempo todo).

Em consoles Windows legados (cp1252) imprimir esses caracteres levanta
UnicodeEncodeError. Reconfigurar para UTF-8 com errors='replace' evita o crash
sem afetar terminais que já são UTF-8 (no-op nesse caso).
"""

from __future__ import annotations

import sys
from typing import TextIO


def force_utf8_stdio() -> None:
    """Reconfigura stdout/stderr para UTF-8. Idempotente e à prova de falha."""
    _reconfigure(sys.stdout)
    _reconfigure(sys.stderr)


def _reconfigure(stream: TextIO | None) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:  # streams sem reconfigure (ex.: captura de teste)
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass
