"""Parse a reply written in the VoiceMate assistant response template.

Research prototype, not production code. Standard library only. The template (see
en/voicemate-assistant/SKILL.md, section 5) has a required <voice> block for text to
speech, an optional <display> block for the screen and a required <actions> block
holding one JSON object. A reply that does not follow it falls back to speaking the
whole text, so a model that forgets the template still gets heard.

  python response_template.py reply.txt      print the parsed parts of a saved reply
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field

_BLOCK = re.compile(r"<(voice|display|actions)>\s*(.*?)\s*</\1>", re.DOTALL)
_TAG = re.compile(r"\[/?(laugh|chuckle|sigh|pause|whisper)\]")


@dataclass
class Reply:
    voice: str
    display: str = ""
    actions: list[dict[str, object]] = field(default_factory=list)
    templated: bool = True
    problems: list[str] = field(default_factory=list)

    def speech(self, expressions: bool) -> str:
        """Voice text for a synthesizer; drops expression tags it cannot render."""
        return self.voice if expressions else " ".join(_TAG.sub("", self.voice).split())


def parse(text: str) -> Reply:
    blocks: dict[str, str] = {}
    for name, body in _BLOCK.findall(text):
        blocks.setdefault(name, body)
    if "voice" not in blocks:
        return Reply(voice=text.strip(), templated=False, problems=["no <voice> block"])

    reply = Reply(voice=blocks["voice"], display=blocks.get("display", ""))
    raw = blocks.get("actions")
    if raw is None:
        reply.problems.append("no <actions> block")
        return reply
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        reply.problems.append(f"actions is not JSON: {exc.msg}")
        return reply
    actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(actions, list):
        reply.problems.append("actions has no list")
        return reply
    reply.actions = [a for a in actions if isinstance(a, dict) and isinstance(a.get("type"), str)]
    if len(reply.actions) != len(actions):
        reply.problems.append("dropped actions without a string type")
    return reply


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    with open(sys.argv[1], encoding="utf-8") as fh:
        reply = parse(fh.read())
    print("templated:", reply.templated)
    print("voice:", reply.speech(expressions=False))
    print("display:", reply.display or "(none)")
    print("actions:", json.dumps(reply.actions, ensure_ascii=False))
    for problem in reply.problems:
        print("problem:", problem)


if __name__ == "__main__":
    main()
