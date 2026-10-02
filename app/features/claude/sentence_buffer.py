"""Aggregate Claude streaming text deltas into complete sentences.

The realtime pipeline speaks **sentence by sentence**: while the 1st sentence
plays, Claude is already generating the next ones. This buffer accumulates the
incoming text chunks and emits a sentence as soon as it detects a boundary
(`. ! ? …` followed by a space, or a line break), avoiding speaking tiny
fragments (e.g. abbreviations like "Dr.").
"""

from __future__ import annotations

import re

# Sentence boundary: one or more terminators, optional closing quotes/parens,
# followed by whitespace. The space is required so we don't split decimal numbers
# ("3.14") nor fire before the sentence has actually ended.
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s")

_DEFAULT_MIN_CHARS = 12


class SentenceBuffer:
    """Accumulate deltas and emit complete sentences. Not thread-safe (single-thread use)."""

    def __init__(self, min_chars: int = _DEFAULT_MIN_CHARS) -> None:
        self._buf = ""
        self._min_chars = min_chars

    def feed(self, delta: str) -> list[str]:
        """Add a delta and return the complete sentences released (may be empty)."""
        self._buf += delta
        out: list[str] = []
        while (sentence := self._extract()) is not None:
            out.append(sentence)
        return out

    def flush(self) -> str | None:
        """Return whatever is left in the buffer (end of stream) and clear it."""
        tail = self._buf.strip()
        self._buf = ""
        return tail or None

    def _extract(self) -> str | None:
        """Extract the next complete sentence, or None if there isn't one yet."""
        for match in _SENTENCE_END.finditer(self._buf):
            end = match.end()
            candidate = self._buf[:end].strip()
            # Short fragments (likely abbreviations) don't become a sentence on
            # their own — move on to the next boundary, accumulating more text.
            if len(candidate) >= self._min_chars:
                self._buf = self._buf[end:].lstrip()
                return candidate
        # A line break is a strong boundary (independent of the minimum length).
        newline = self._buf.find("\n")
        if newline != -1:
            candidate = self._buf[:newline].strip()
            self._buf = self._buf[newline + 1 :].lstrip()
            if candidate:
                return candidate
        return None
