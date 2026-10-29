from __future__ import annotations

import pytest

from app.companion.assistant.commands import (
    Ambiguous,
    BackToAssistant,
    DirectTo,
    NotFound,
    ReadLastAnswer,
    SendToConversation,
    StopSpeaking,
    SwitchConversation,
    detect,
    normalize,
)

TITLES = ["Projeto Alfa", "Projeto Beta", "Compras", "VoiceMate"]


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Lê a última resposta", "pt-BR"),
        ("leia a ultima resposta, por favor.", "pt"),
        ("LEIA A ÚLTIMA RESPOSTA!", "pt-BR"),
        ("Read the last answer", "en"),
        ("read the last answer please", "en-US"),
        ("Lee la última respuesta", "es"),
        ("lee la ultima respuesta", "es-419"),
    ],
)
def test_read_last_answer(text: str, language: str) -> None:
    assert detect(text, language, TITLES) == ReadLastAnswer()


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Para de falar", "pt-BR"),
        ("pare de falar agora", "pt"),
        ("Stop talking", "en"),
        ("stop speaking!", "en-GB"),
        ("Deja de hablar", "es"),
        ("DEJA DE HABLAR.", "es-ES"),
    ],
)
def test_stop_speaking(text: str, language: str) -> None:
    assert detect(text, language, TITLES) == StopSpeaking()


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Troca para a conversa projeto alfa", "pt-BR"),
        ("vai para a conversa Projeto Alfa.", "pt"),
        ("Switch to conversation project alfa", "en"),
        ("go to the Projeto Alfa conversation", "en-US"),
        ("Cambia a la conversación projeto alfa", "es"),
    ],
)
def test_switch_conversation(text: str, language: str) -> None:
    assert detect(text, language, TITLES) == SwitchConversation("Projeto Alfa")


def test_switch_is_accent_insensitive_for_titles() -> None:
    titles = ["Reunião de equipe", "Compras"]
    assert detect("troca para a conversa reuniao de equipe", "pt", titles) == SwitchConversation("Reunião de equipe")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Manda para a conversa Compras dizendo leite e pão", "pt-BR"),
        ("manda para a conversa compras: leite e pão", "pt"),
        ("Send to conversation Compras saying leite e pão", "en"),
        ("send to the Compras conversation: leite e pão", "en-US"),
        ("Manda a la conversación compras diciendo leite e pão", "es"),
    ],
)
def test_send_to_conversation(text: str, language: str) -> None:
    assert detect(text, language, TITLES) == SendToConversation("Compras", "leite e pão")


def test_send_body_keeps_raw_text_and_may_be_long() -> None:
    body = "Compra 2 litros de leite, pão e ovos: e não esquece o café da manhã de amanhã cedo"
    result = detect(f"manda para a conversa compras dizendo {body}", "pt-BR", TITLES)
    assert result == SendToConversation("Compras", body)


def test_send_without_body_is_not_a_command() -> None:
    assert detect("manda para a conversa compras dizendo", "pt", TITLES) is None
    assert detect("manda para a conversa compras", "pt", TITLES) is None


def test_normalize() -> None:
    assert normalize("  Lê,  a ÚLTIMA   resposta!! ") == "le a ultima resposta"
    assert normalize("São João, e-mail") == "sao joao e mail"


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("eu acho que você deve ler a última resposta depois", "pt-BR"),
        ("please read the last answer", "en"),
        ("ele disse para de falar comigo", "pt"),
        ("I told him to stop talking", "en"),
        ("ayer le dije deja de hablar", "es"),
        ("por favor troca para a conversa projeto alfa", "pt"),
        ("tell me about the go to the Projeto Alfa conversation", "en"),
    ],
)
def test_trigger_in_the_middle_is_not_a_command(text: str, language: str) -> None:
    assert detect(text, language, TITLES) is None


def test_trigger_followed_by_unrelated_words_is_not_read_or_stop() -> None:
    assert detect("read the last answer of the exam and then compare it", "en", TITLES) is None
    assert detect("para de falar disso e vamos ao próximo assunto", "pt", TITLES) is None


def test_word_cap() -> None:
    long_title = ["alfa " * 20]
    text = "troca para a conversa " + "alfa " * 20
    assert detect(text, "pt", long_title) is None


def test_ambiguous_titles() -> None:
    result = detect("troca para a conversa projeto", "pt", TITLES)
    assert isinstance(result, Ambiguous)
    assert result.kind == "switch"
    assert result.query == "projeto"
    assert set(result.candidates) == {"Projeto Alfa", "Projeto Beta"}


def test_ambiguous_send() -> None:
    result = detect("send to conversation projeto saying hello", "en", TITLES)
    assert isinstance(result, Ambiguous)
    assert result.kind == "send"
    assert set(result.candidates) == {"Projeto Alfa", "Projeto Beta"}


def test_exact_title_wins_over_longer_title() -> None:
    titles = ["Alfa", "Projeto Alfa"]
    assert detect("switch to conversation alfa", "en", titles) == SwitchConversation("Alfa")


def test_not_found() -> None:
    assert detect("troca para a conversa xadrez", "pt", TITLES) == NotFound("switch", "xadrez")
    assert detect("manda para a conversa xadrez dizendo oi", "pt", TITLES) == NotFound("send", "xadrez")


def test_not_found_without_titles() -> None:
    assert detect("switch to conversation alfa", "en", []) == NotFound("switch", "alfa")


def test_small_typo_still_matches() -> None:
    assert detect("troca para a conversa compra", "pt", TITLES) == SwitchConversation("Compras")


def test_unknown_language_falls_back_to_english() -> None:
    assert detect("read the last answer", "fr-FR", TITLES) == ReadLastAnswer()
    assert detect("stop talking", "", TITLES) == StopSpeaking()
    assert detect("lê a última resposta", "xx", TITLES) is None


def test_language_tables_are_separate() -> None:
    assert detect("read the last answer", "pt-BR", TITLES) is None
    assert detect("para de falar", "en", TITLES) is None


@pytest.mark.parametrize("text", ["", "   ", "...", "?!"])
def test_empty_text(text: str) -> None:
    assert detect(text, "pt-BR", TITLES) is None


def test_ordinary_speech_is_not_a_command() -> None:
    assert detect("qual é a previsão do tempo para amanhã", "pt-BR", TITLES) is None


# --- direct mode -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("fala direto com a conversa projeto alfa", "pt-BR"),
        ("Talk directly to the conversation Projeto Alfa", "en"),
        ("habla directamente con la conversacion projeto alfa", "es"),
    ],
)
def test_direct_to_a_conversation(text: str, language: str) -> None:
    assert detect(text, language, TITLES) == DirectTo("Projeto Alfa")


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("volta para o assistente", "pt-BR"),
        ("Back to the assistant.", "en"),
        ("vuelve al asistente", "es"),
    ],
)
def test_back_to_the_assistant_only_counts_in_direct_mode(text: str, language: str) -> None:
    assert detect(text, language, TITLES, direct=True) == BackToAssistant()
    assert detect(text, language, TITLES) is None


def test_back_keeps_what_follows_for_the_assistant() -> None:
    assert detect("new subject, what is on my calendar?", "en", TITLES, direct=True) == BackToAssistant(
        "what is on my calendar?"
    )
    assert detect("novo assunto: compra pão", "pt-BR", TITLES, direct=True) == BackToAssistant("compra pão")


def test_direct_to_an_ambiguous_title_names_the_candidates() -> None:
    command = detect("talk directly to the conversation projeto", "en", TITLES)
    assert isinstance(command, Ambiguous)
    assert set(command.candidates) == {"Projeto Alfa", "Projeto Beta"}
