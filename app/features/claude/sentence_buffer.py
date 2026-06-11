"""Agrega deltas de texto do streaming do Claude em frases completas.

O pipeline realtime fala **frase a frase**: enquanto a 1ª frase toca, o Claude já
gera as seguintes. Este buffer acumula os pedaços de texto que chegam e emite uma
frase assim que detecta uma fronteira (`. ! ? …` seguidos de espaço, ou quebra de
linha), evitando falar fragmentos minúsculos (ex.: abreviações como "Dr.").
"""

from __future__ import annotations

import re

# Fronteira de frase: um ou mais terminadores, possíveis aspas/parênteses de
# fechamento, seguidos de espaço em branco. Exige o espaço para não cortar
# números decimais ("3.14") nem disparar antes de a frase realmente terminar.
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s")

_DEFAULT_MIN_CHARS = 12


class SentenceBuffer:
    """Acumula deltas e emite frases completas. Não é thread-safe (uso single-thread)."""

    def __init__(self, min_chars: int = _DEFAULT_MIN_CHARS) -> None:
        self._buf = ""
        self._min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        """Adiciona um delta e retorna as frases completas liberadas (pode ser vazio)."""
        self._buf += delta
        out: list[str] = []
        while (sentence := self._extract()) is not None:
            out.append(sentence)
        return out

    def flush(self) -> str | None:
        """Devolve o que sobrou no buffer (fim do stream) e o esvazia."""
        tail = self._buf.strip()
        self._buf = ""
        return tail or None

    def _extract(self) -> str | None:
        """Extrai a próxima frase completa, ou None se ainda não há uma."""
        for match in _SENTENCE_END.finditer(self._buf):
            end = match.end()
            candidate = self._buf[:end].strip()
            # Fragmentos curtos (provável abreviação) não viram frase sozinhos —
            # seguimos para a próxima fronteira, acumulando mais texto.
            if len(candidate) >= self._min_chars:
                self._buf = self._buf[end:].lstrip()
                return candidate
        # Quebra de linha é fronteira forte (independe do tamanho mínimo).
        newline = self._buf.find("\n")
        if newline != -1:
            candidate = self._buf[:newline].strip()
            self._buf = self._buf[newline + 1 :].lstrip()
            if candidate:
                return candidate
        return None
