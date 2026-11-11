"""The person's standing instructions to the assistant: rules of behavior that outlive a session.

When the person tells the assistant how to behave from now on ("don't ask before reading a
conversation", "always answer in one sentence"), the central emits a `remember` action with the
rule in its own words; `forget` drops one. VoiceMate keeps them in `assistant-instructions.json`
under its state dir and writes them to assistant.json (`instructions`), which the skill reads at
the start of every session and after a compaction, so a rule given once holds in the next
conversation too. The central never writes the file itself (VoiceMate files are read only for it).

A rule is one line of plain text, at most `TEXT_LIMIT` characters; the same rule (ignoring case
and spacing) is kept once, and beyond `LIMIT` rules the oldest go first. `forget` takes the exact
text, or a fragment that matches exactly one rule. Never raises on a bad file. Thread-safe,
standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

log = logging.getLogger(__name__)

VERSION: Final = 1
LIMIT: Final = 40
TEXT_LIMIT: Final = 300

ForgetResult = Literal["removed", "missing", "ambiguous"]


@dataclass(frozen=True)
class Instruction:
    text: str
    since: float  # time.time()


def _one_line(text: str) -> str:
    return " ".join(text.split())[:TEXT_LIMIT]


def _key(text: str) -> str:
    return _one_line(text).casefold()


class StandingInstructions:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._items: list[Instruction] = self._load()

    def items(self) -> tuple[Instruction, ...]:
        with self._lock:
            return tuple(self._items)

    def add(self, text: object) -> bool:
        """Keep a rule. False when it is empty, not text, or kept already."""
        if not isinstance(text, str) or not _one_line(text):
            return False
        line = _one_line(text)
        with self._lock:
            if any(_key(item.text) == _key(line) for item in self._items):
                return False
            self._items.append(Instruction(line, self._clock()))
            del self._items[:-LIMIT]
            self._save()
        return True

    def forget(self, text: object) -> ForgetResult:
        """Drop the rule with that text, or the only rule that contains it."""
        if not isinstance(text, str) or not _one_line(text):
            return "missing"
        wanted = _key(text)
        with self._lock:
            exact = [item for item in self._items if _key(item.text) == wanted]
            matches = exact or [item for item in self._items if wanted in _key(item.text)]
            if not matches:
                return "missing"
            if len(matches) > 1:
                return "ambiguous"
            self._items.remove(matches[0])
            self._save()
        return "removed"

    def as_json(self) -> list[dict[str, str]]:
        """The `instructions` list of assistant.json."""
        return [
            {"text": item.text, "since": datetime.fromtimestamp(item.since, UTC).isoformat(timespec="seconds")}
            for item in self.items()
        ]

    def _load(self) -> list[Instruction]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries = raw.get("instructions") if isinstance(raw, dict) else None
        loaded: list[Instruction] = []
        for item in entries if isinstance(entries, list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                continue
            line = _one_line(item["text"])
            since = item.get("since")
            if line and not any(_key(kept.text) == _key(line) for kept in loaded):
                stamp = float(since) if isinstance(since, int | float) and not isinstance(since, bool) else 0.0
                loaded.append(Instruction(line, stamp))
        return loaded[-LIMIT:]

    def _save(self) -> None:
        payload = json.dumps(
            {"version": VERSION, "instructions": [{"text": i.text, "since": i.since} for i in self._items]},
            ensure_ascii=False,
            indent=1,
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".instructions.", suffix=".tmp")
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                    file.write(payload + "\n")
                tmp.replace(self._path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        except OSError as exc:
            log.warning("assistant: cannot save the standing instructions %s (%s)", self._path, exc)
