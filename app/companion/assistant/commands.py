"""Voice command detector: pure text in, a typed command (or nothing) out.

No LLM and no UI: the text is normalized (case, accents, punctuation) and must START with a
trigger phrase, so ordinary speech that merely contains a trigger word is never a command.
Trigger tables per language are data, not UI strings (docs/research/voice-assistant-design.md,
section 6). Conversation titles are matched fuzzily; a doubtful match is reported as
`Ambiguous` or `NotFound` so the caller does nothing instead of guessing.

Direct mode (talk straight to one conversation) starts with `DirectTo`; `BackToAssistant` ends
it and is only recognized while it lasts (`detect(..., direct=True)`): outside it, "new subject"
is ordinary speech for the assistant.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Final, Literal

TitleKind = Literal["switch", "send", "direct"]
Lang = Literal["pt", "en", "es"]

MAX_COMMAND_WORDS: Final = 12
MIN_TITLE_SCORE: Final = 0.75
MIN_TITLE_MARGIN: Final = 0.1
_EPSILON: Final = 1e-9


@dataclass(frozen=True)
class ReadLastAnswer:
    """Read the last assistant answer aloud."""


@dataclass(frozen=True)
class StopSpeaking:
    """Stop the speech in progress."""


@dataclass(frozen=True)
class SwitchConversation:
    """Make the text screen follow the conversation with this (original) title."""

    title: str


@dataclass(frozen=True)
class SendToConversation:
    """Delegate a message to the conversation with this (original) title."""

    title: str
    body: str


@dataclass(frozen=True)
class DirectTo:
    """From now on every prompt goes verbatim to the conversation with this (original) title."""

    title: str


@dataclass(frozen=True)
class BackToAssistant:
    """Leave direct mode; `rest` is what followed the phrase ("new subject, ..."), for the assistant."""

    rest: str = ""


@dataclass(frozen=True)
class Ambiguous:
    """Two or more titles are plausible: nothing must be done."""

    kind: TitleKind
    query: str
    candidates: tuple[str, ...]


@dataclass(frozen=True)
class NotFound:
    """No title is close enough to the spoken query."""

    kind: TitleKind
    query: str


Command = (
    ReadLastAnswer
    | StopSpeaking
    | SwitchConversation
    | SendToConversation
    | DirectTo
    | BackToAssistant
    | Ambiguous
    | NotFound
)


@dataclass(frozen=True)
class _Pattern:
    """A trigger: `prefix` words at the start, then the title, then optional `suffix` words."""

    prefix: tuple[str, ...]
    suffix: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Triggers:
    read: tuple[tuple[str, ...], ...]
    stop: tuple[tuple[str, ...], ...]
    switch: tuple[_Pattern, ...]
    send: tuple[_Pattern, ...]
    direct: tuple[_Pattern, ...]
    back: tuple[tuple[str, ...], ...]
    separator: str
    fillers: frozenset[str]


def _words(*phrases: str) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(p.split()) for p in phrases)


def _patterns(*specs: str) -> tuple[_Pattern, ...]:
    """Each spec is "prefix words" or "prefix words | suffix words", already normalized."""
    out: list[_Pattern] = []
    for spec in specs:
        prefix, _, suffix = spec.partition("|")
        out.append(_Pattern(tuple(prefix.split()), tuple(suffix.split())))
    return tuple(out)


_TRIGGERS: Final[dict[Lang, _Triggers]] = {
    "pt": _Triggers(
        read=_words(
            "le a ultima resposta",
            "leia a ultima resposta",
            "ler a ultima resposta",
            "le a ultima mensagem",
            "leia a ultima mensagem",
            "repete a ultima resposta",
            "repita a ultima resposta",
        ),
        stop=_words(
            "para de falar",
            "pare de falar",
            "para de ler",
            "pare de ler",
            "silencio",
        ),
        switch=_patterns(
            "troca para a conversa",
            "troque para a conversa",
            "trocar para a conversa",
            "troca pra conversa",
            "muda para a conversa",
            "mude para a conversa",
            "vai para a conversa",
            "vai pra conversa",
            "va para a conversa",
            "ir para a conversa",
            "abre a conversa",
            "abra a conversa",
        ),
        send=_patterns(
            "manda para a conversa",
            "mande para a conversa",
            "manda pra conversa",
            "manda na conversa",
            "envia para a conversa",
            "envie para a conversa",
            "escreve na conversa",
            "escreva na conversa",
        ),
        direct=_patterns(
            "fala direto com a conversa",
            "fale direto com a conversa",
            "falar direto com a conversa",
            "fala diretamente com a conversa",
            "fale diretamente com a conversa",
            "fala direto na conversa",
            "conversa direto com a conversa",
        ),
        back=_words(
            "volta para o assistente",
            "volta para a assistente",
            "volta pro assistente",
            "volta pra assistente",
            "volte para o assistente",
            "volte para a assistente",
            "voltar para o assistente",
            "voltar para a assistente",
            "novo assunto",
            "outro assunto",
            "mudando de assunto",
        ),
        separator="dizendo",
        fillers=frozenset({"por", "favor", "agora", "pra", "mim", "ai"}),
    ),
    "en": _Triggers(
        read=_words(
            "read the last answer",
            "read last answer",
            "read the last reply",
            "read the last message",
            "read me the last answer",
            "read back the last answer",
        ),
        stop=_words(
            "stop talking",
            "stop speaking",
            "stop reading",
            "be quiet",
        ),
        switch=_patterns(
            "switch to conversation",
            "switch to the conversation",
            "switch to the | conversation",
            "go to conversation",
            "go to the conversation",
            "go to the | conversation",
            "change to conversation",
            "change to the conversation",
            "open conversation",
            "open the conversation",
            "open the | conversation",
        ),
        send=_patterns(
            "send to conversation",
            "send to the conversation",
            "send to the | conversation",
            "send a message to conversation",
            "send a message to the conversation",
            "write to conversation",
            "write to the conversation",
        ),
        direct=_patterns(
            "talk directly to conversation",
            "talk directly to the conversation",
            "talk directly to the | conversation",
            "speak directly to the conversation",
            "talk straight to the conversation",
        ),
        back=_words(
            "back to the assistant",
            "go back to the assistant",
            "return to the assistant",
            "new subject",
            "new topic",
            "change of subject",
        ),
        separator="saying",
        fillers=frozenset({"please", "now", "for", "me"}),
    ),
    "es": _Triggers(
        read=_words(
            "lee la ultima respuesta",
            "lea la ultima respuesta",
            "leer la ultima respuesta",
            "lee el ultimo mensaje",
            "repite la ultima respuesta",
        ),
        stop=_words(
            "deja de hablar",
            "para de hablar",
            "deja de leer",
            "para de leer",
            "calla",
            "silencio",
        ),
        switch=_patterns(
            "cambia a la conversacion",
            "cambiar a la conversacion",
            "ve a la conversacion",
            "ir a la conversacion",
            "pasa a la conversacion",
            "abre la conversacion",
        ),
        send=_patterns(
            "manda a la conversacion",
            "mande a la conversacion",
            "envia a la conversacion",
            "envia un mensaje a la conversacion",
            "escribe en la conversacion",
        ),
        direct=_patterns(
            "habla directamente con la conversacion",
            "hable directamente con la conversacion",
            "hablar directamente con la conversacion",
            "habla directo con la conversacion",
        ),
        back=_words(
            "vuelve al asistente",
            "vuelve a la asistente",
            "volver al asistente",
            "regresa al asistente",
            "nuevo tema",
            "cambio de tema",
        ),
        separator="diciendo",
        fillers=frozenset({"por", "favor", "ahora", "para", "mi"}),
    ),
}

_BODY_STRIP: Final = " \t\r\n,:;.-!?"


def _language(tag: str) -> Lang:
    """Map a BCP 47 style tag ("pt-BR", "es_419", "en-US") to a trigger table; default en."""
    primary = re.split(r"[-_]", tag.strip().lower(), maxsplit=1)[0]
    if primary == "pt":
        return "pt"
    if primary == "es":
        return "es"
    return "en"


def normalize(text: str) -> str:
    """Lowercase, strip accents, replace punctuation by spaces and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]|_", " ", stripped).split())


def detect(text: str, language: str, titles: Sequence[str], *, direct: bool = False) -> Command | None:
    """Return the command spoken in `text`, or None when it is an ordinary prompt. `direct`:
    direct mode is on, so the phrases that end it count."""
    triggers = _TRIGGERS[_language(language)]
    tokens = normalize(text).split()
    if not tokens:
        return None
    if direct:
        back = _back(text, tokens, triggers)
        if back is not None:
            return back
    send = _detect_send(text, triggers, titles)
    if send is not None:
        return send
    if len(tokens) > MAX_COMMAND_WORDS:
        return None
    if _is_phrase(tokens, triggers.stop, triggers.fillers):
        return StopSpeaking()
    if _is_phrase(tokens, triggers.read, triggers.fillers):
        return ReadLastAnswer()
    query = _query_after(tokens, triggers.direct)
    if query:
        return _resolve(query, "direct", titles, None)
    query = _query_after(tokens, triggers.switch)
    if query:
        return _resolve(query, "switch", titles, None)
    return None


def _back(text: str, tokens: list[str], triggers: _Triggers) -> BackToAssistant | None:
    """A back phrase at the start; the original words after it are kept for the assistant."""
    for phrase in sorted(triggers.back, key=len, reverse=True):
        if tuple(tokens[: len(phrase)]) != phrase:
            continue
        rest_tokens = tokens[len(phrase) :]
        if all(t in triggers.fillers for t in rest_tokens):
            return BackToAssistant()
        return BackToAssistant(_after_words(text, len(phrase)))
    return None


def _after_words(text: str, count: int) -> str:
    """The original text after its first `count` normalized words (punctuation trimmed)."""
    seen = 0
    for match in re.finditer(r"\w+", text):
        seen += len(normalize(match.group(0)).split())
        if seen >= count:
            return text[match.end() :].lstrip(_BODY_STRIP).strip()
    return ""


def may_need_titles(text: str, language: str) -> bool:
    """Cheap check: True when `text` starts like a switch or send command, so titles may matter."""
    triggers = _TRIGGERS[_language(language)]
    tokens = normalize(text).split()
    if not tokens:
        return False
    if len(tokens) <= MAX_COMMAND_WORDS and (
        _query_after(tokens, triggers.switch) or _query_after(tokens, triggers.direct)
    ):
        return True
    return _send_head(text, triggers) is not None


def _is_phrase(tokens: list[str], phrases: tuple[tuple[str, ...], ...], fillers: frozenset[str]) -> bool:
    """True when the text is a trigger phrase followed only by polite filler words."""
    for phrase in phrases:
        if tuple(tokens[: len(phrase)]) == phrase and all(t in fillers for t in tokens[len(phrase) :]):
            return True
    return False


def _query_after(tokens: list[str], patterns: tuple[_Pattern, ...]) -> str:
    """The title words between a pattern's prefix and suffix, or "" when nothing matches."""
    for pattern in sorted(patterns, key=lambda p: len(p.prefix) + len(p.suffix), reverse=True):
        n = len(pattern.prefix)
        if tuple(tokens[:n]) != pattern.prefix:
            continue
        rest = tokens[n:]
        if pattern.suffix:
            m = len(pattern.suffix)
            if tuple(rest[-m:]) != pattern.suffix:
                continue
            rest = rest[:-m]
        if rest:
            return " ".join(rest)
    return ""


def _detect_send(text: str, triggers: _Triggers, titles: Sequence[str]) -> Command | None:
    """A send command needs a separator (the language's word or a colon) and a body."""
    parts = _send_head(text, triggers)
    if parts is None:
        return None
    query, body = parts
    return _resolve(query, "send", titles, body)


def _send_head(text: str, triggers: _Triggers) -> tuple[str, str] | None:
    """The (title query, body) of a send command, or None when `text` is not shaped like one."""
    raw = text.strip()
    colon = raw.find(":")
    word = re.search(rf"\b{re.escape(triggers.separator)}\b", raw, re.IGNORECASE)
    if colon < 0 and word is None:
        return None
    if word is not None and (colon < 0 or word.start() < colon):
        head, tail = raw[: word.start()], raw[word.end() :]
    else:
        head, tail = raw[:colon], raw[colon + 1 :]
    body = tail.lstrip(_BODY_STRIP).strip()
    head_tokens = normalize(head).split()
    if not body or len(head_tokens) > MAX_COMMAND_WORDS:
        return None
    query = _query_after(head_tokens, triggers.send)
    if not query:
        return None
    return query, body


def _resolve(query: str, kind: TitleKind, titles: Sequence[str], body: str | None) -> Command:
    """Pick the title for `query`, or report why none can be picked."""
    scored = sorted(((_score(query, normalize(t)), t) for t in titles), key=lambda s: s[0], reverse=True)
    if not scored or scored[0][0] < MIN_TITLE_SCORE:
        return NotFound(kind, query)
    best_score, best_title = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_score - second >= MIN_TITLE_MARGIN - _EPSILON:
        if kind == "direct":
            return DirectTo(best_title)
        if body is None:
            return SwitchConversation(best_title)
        return SendToConversation(best_title, body)
    close = tuple(t for s, t in scored if s >= best_score - MIN_TITLE_MARGIN + _EPSILON)
    return Ambiguous(kind, query, close)


def _score(query: str, title: str) -> float:
    """Similarity in [0, 1]: sequence ratio, lifted when every query word is in the title."""
    if not query or not title:
        return 0.0
    if query == title:
        return 1.0
    ratio = SequenceMatcher(None, query, title).ratio()
    query_tokens = query.split()
    title_tokens = title.split()
    if all(t in title_tokens for t in query_tokens):
        ratio = max(ratio, 0.75 + 0.2 * len(query_tokens) / len(title_tokens))
    return ratio
