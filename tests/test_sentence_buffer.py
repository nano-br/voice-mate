"""Agregação de deltas em frases (streaming IA→fala)."""

from __future__ import annotations

from app.features.claude.sentence_buffer import SentenceBuffer


def test_emits_sentence_only_after_trailing_space() -> None:
    buf = SentenceBuffer(min_chars=1)
    # Sem espaço após o terminador, ainda não emite (pode haver mais texto).
    assert buf.feed("Olá mundo.") == []
    # O espaço fecha a fronteira.
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
    # "Dr." (3 chars) é curto demais p/ virar frase — segue até a próxima fronteira.
    out = buf.feed("Dr. Silva chegou agora. ")
    assert out == ["Dr. Silva chegou agora."]


def test_newline_is_a_strong_boundary() -> None:
    buf = SentenceBuffer(min_chars=50)
    # Quebra de linha emite independentemente do tamanho mínimo.
    out = buf.feed("linha um\nresto ")
    assert out == ["linha um"]
    assert buf.flush() == "resto"


def test_does_not_split_decimal_numbers() -> None:
    buf = SentenceBuffer(min_chars=1)
    # "3.14" não tem espaço após o ponto → não vira fronteira.
    assert buf.feed("O valor é 3.14 ") == []
    assert buf.flush() == "O valor é 3.14"
