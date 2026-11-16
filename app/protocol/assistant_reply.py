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
    "open_conversation",
    "notify",
    "follow_up",
    "follow_up_done",
    "focus",
    "set_setting",
    "read_inbox",
    "clear_inbox",
    "remember",
    "forget",
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
# For a reply without a <voice> block: its prose is spoken, so Markdown marks must not be read out.
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_CODE = re.compile(r"```.*?(```|\Z)", re.DOTALL)
_MD_URL = re.compile(r"https?://\S+")
_MD_MARKS = re.compile(r"[*`|]+|^\s*[#>]+|(?<!\w)_+|_+(?!\w)", re.MULTILINE)
_SENTENCE_END = re.compile(r"[.!?…](?=\s|\Z)")
FALLBACK_VOICE_LIMIT: Final = 600  # characters of prose spoken for a reply without a <voice> block
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
    degraded: bool = False  # no <voice> block: the prose is shown, and spoken as `spoken_fallback`
    spoken_fallback: str = ""  # degraded only: the start of the prose, Markdown marks removed
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
        # The person still gets the answer: the blocks that are there are honored, the prose is
        # shown without the raw <actions> JSON, and its start is offered for speaking.
        degraded_problems = ["no <voice> block"]
        degraded_actions: tuple[Action, ...] = ()
        if "actions" in blocks:
            degraded_actions = _parse_actions(blocks["actions"], degraded_problems)
        prose = _BLOCK.sub("", text).strip()
        shown = "\n\n".join(part for part in (prose, blocks.get("display", "")) if part)
        return ParsedReply(
            voice="",
            voice_raw="",
            display=shown,
            actions=degraded_actions,
            degraded=True,
            spoken_fallback=plain_speech(prose or blocks.get("display", "")),
            problems=tuple(degraded_problems),
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


def plain_speech(text: str, limit: int = FALLBACK_VOICE_LIMIT) -> str:
    """`text` as something to say: Markdown marks removed, one line, cut at a sentence end near `limit`."""
    prose = _MD_URL.sub(" ", _MD_LINK.sub(r"\1", _MD_CODE.sub(" ", text)))
    spoken = " ".join(_MD_MARKS.sub(" ", prose).split())
    if len(spoken) <= limit:
        return spoken
    ends = [match.end() for match in _SENTENCE_END.finditer(spoken, 0, limit)]
    return spoken[: ends[-1]] if ends else spoken[:limit].rsplit(" ", 1)[0]


# Actions whose one required field is the conversation: a bare string there is its id.
_BY_CONVERSATION: Final = frozenset({"open_conversation", "follow_up", "follow_up_done", "focus"})
_BARE: Final = frozenset({"read_inbox", "clear_inbox"})  # actions that need no field


def _without_envelope(data: dict[str, object]) -> list[object] | None:
    """The actions of an object written without `version`: its `actions` list, the object itself
    when it is one action, or one action per key that names an action type (`{"follow_up":
    {...}}`; a bare string there is the conversation id). None when it is none of these."""
    listed = data.get("actions")
    if isinstance(listed, list):
        return listed
    if isinstance(data.get("type"), str):
        return [data]
    entries: list[object] = []
    for key, value in data.items():
        if key not in ACTION_TYPES:
            continue
        if isinstance(value, dict):
            entries.append({**value, "type": key})
        elif isinstance(value, str) and key in _BY_CONVERSATION:
            entries.append({"type": key, "session_id": value})
        elif key in _BARE:
            entries.append({"type": key})
    return entries or None


def _parse_actions(raw: str, problems: list[str]) -> tuple[Action, ...]:
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        problems.append("actions is not valid JSON")
        return ()
    entries: object
    if isinstance(data, list):
        entries = data  # the bare list of actions, without the envelope: what was meant is clear
    elif not isinstance(data, dict):
        problems.append("actions is not a JSON object")
        return ()
    elif "version" not in data:
        # The envelope was forgotten (a central whose conversation was summarized tends to): what
        # was meant is still clear, and dropping it loses a wait the person is counting on.
        entries = _without_envelope(data)
        if entries is None:
            problems.append("unsupported actions version: None")
            return ()
        problems.append("actions without the envelope")
    else:
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
        if "session_id" not in params and "conversation_id" in params:
            params["session_id"] = params.pop("conversation_id")  # the name the conversation tools use
        parsed.append(Action(type=kind, params=params))  # type: ignore[arg-type]
    return tuple(parsed)
