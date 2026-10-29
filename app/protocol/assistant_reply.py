"""Parser for the assistant response template (voice, display, actions blocks).

Stdlib only, like the rest of app.protocol. The skill voicemate-assistant defines the
template: a required <voice> block for text to speech, an optional <display> block for
the text screen and an <actions> block holding one JSON object. Parsing never raises: a
reply that breaks the template still comes back as a ParsedReply, with `problems`
saying what was wrong. A reply without <voice> is shown, not spoken.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Final, Literal, get_args

ActionType = Literal[
    "open_conversation", "notify", "follow_up", "follow_up_done", "focus", "set_setting", "read_inbox", "clear_inbox"
]
ACTION_TYPES: Final[frozenset[str]] = frozenset(get_args(ActionType))
ACTIONS_VERSION: Final = 1

# An opening tag, then the body up to its closing tag. A closing tag missing at the very
# end of the text (the model was cut off) is tolerated: the body runs to the end.
_BLOCK = re.compile(
    r"<(voice|display|actions)\s*>(.*?)(?:</\1\s*>|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# A closing tag cut in the middle ("</actio") left at the end of a truncated body.
_PARTIAL_CLOSE = re.compile(r"</[A-Za-z]*\s*\Z")
# Single-word bracket tags such as [laugh] or [/whisper]; "[see note 2]" is not one.
_EXPRESSION = re.compile(r"\[/?[^\W\d_]+\]")
_TAG_BEFORE_PUNCT = re.compile(r"\s*\[/?[^\W\d_]+\](?=\s*[,.;:!?])")


@dataclass(frozen=True)
class Action:
    """One entry of the <actions> list; `params` holds every key except `type`."""

    type: ActionType
    params: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedReply:
    voice: str  # what to speak, expression tags stripped
    voice_raw: str  # the voice block as written, expression tags kept
    display: str
    actions: tuple[Action, ...] = ()
    degraded: bool = False  # no <voice> block: the text is shown, not spoken
    problems: tuple[str, ...] = ()


def strip_expressions(voice: str) -> str:
    """Remove single-word bracket tags and normalize whitespace."""
    voice = _TAG_BEFORE_PUNCT.sub("", voice)
    return " ".join(_EXPRESSION.sub(" ", voice).split())


def parse_reply(text: str) -> ParsedReply:
    """Split a reply into its blocks. Never raises, whatever the input."""
    try:
        return _parse(text if isinstance(text, str) else str(text))
    except Exception as exc:  # defensive: a parser slip must never lose the reply
        shown = text.strip() if isinstance(text, str) else ""
        return ParsedReply(
            voice="",
            voice_raw="",
            display=shown,
            degraded=True,
            problems=(f"parser failure: {type(exc).__name__}",),
        )


def _parse(text: str) -> ParsedReply:
    blocks: dict[str, str] = {}
    for match in _BLOCK.finditer(text):
        body = _PARTIAL_CLOSE.sub("", match.group(2)).strip()
        blocks.setdefault(match.group(1).lower(), body)

    if "voice" not in blocks:
        return ParsedReply(
            voice="",
            voice_raw="",
            display=text.strip(),
            degraded=True,
            problems=("no <voice> block",),
        )

    problems: list[str] = []
    actions: tuple[Action, ...] = ()
    raw_actions = blocks.get("actions")
    if raw_actions is None:
        problems.append("no <actions> block")
    else:
        actions = _parse_actions(raw_actions, problems)

    voice_raw = blocks["voice"]
    return ParsedReply(
        voice=strip_expressions(voice_raw),
        voice_raw=voice_raw,
        display=blocks.get("display", ""),
        actions=actions,
        problems=tuple(problems),
    )


def _parse_actions(raw: str, problems: list[str]) -> tuple[Action, ...]:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        problems.append("actions is not valid JSON")
        return ()
    if not isinstance(data, dict):
        problems.append("actions is not a JSON object")
        return ()
    version = data.get("version")
    if isinstance(version, bool) or version != ACTIONS_VERSION:
        problems.append(f"unsupported actions version: {version!r}")
        return ()
    entries = data.get("actions")
    if not isinstance(entries, list):
        problems.append("actions has no list")
        return ()

    parsed: list[Action] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("type"), str):
            problems.append("dropped an action without a string type")
            continue
        kind = entry["type"]
        if kind not in ACTION_TYPES:
            problems.append(f"dropped unknown action type: {kind!r}")
            continue
        params = {key: value for key, value in entry.items() if key != "type"}
        parsed.append(Action(type=kind, params=params))  # type: ignore[arg-type]
    return tuple(parsed)
