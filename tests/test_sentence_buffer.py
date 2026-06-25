"""Aggregating deltas into sentences (AI→speech streaming)."""

from __future__ import annotations

from app.features.claude.sentence_buffer import SentenceBuffer


def test_emits_sentence_only_after_trailing_space() -> None:
    buf = SentenceBuffer(min_chars=1)
    # Without a space after the terminator, it does not emit yet (there may be more text).
    assert buf.feed("Olá mundo.") == []
    # The space closes the boundary.
    assert buf.feed(" Tudo certo") == ["Olá mundo."]


def test_flush_returns_remainder() -> None:
    buf = SentenceBuffer(min_chars=1)
    buf.feed("Frase sem ponto final")
    assert buf.flush() == "Frase sem ponto final"
    assert buf.flush() is None


def test_multiple_sentences_in_one_feed() -> None:
    buf = SentenceBuffer(min_chars=1)
    out = buf.feed("Primeira frase. Segunda frase! Terceira? ")
    assert out == ["Primeira frase.", "Segunda frase!", "Terceira?"]


def test_min_chars_holds_abbreviation() -> None:
    buf = SentenceBuffer(min_chars=12)
    # "Dr." (3 chars) is too short to become a sentence — continues to the next boundary.
    out = buf.feed("Dr. Silva chegou agora. ")
    assert out == ["Dr. Silva chegou agora."]


def test_newline_is_a_strong_boundary() -> None:
    buf = SentenceBuffer(min_chars=50)
    # A newline emits regardless of the minimum length.
    out = buf.feed("linha um\nresto ")
    assert out == ["linha um"]
    assert buf.flush() == "resto"


def test_does_not_split_decimal_numbers() -> None:
    buf = SentenceBuffer(min_chars=1)
    # "3.14" has no space after the dot → does not become a boundary.
    assert buf.feed("O valor é 3.14 ") == []
    assert buf.flush() == "O valor é 3.14"
