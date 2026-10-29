"""Disk reader for Claude Code sessions: follow a conversation without spawning the CLI.

Every Claude Code client (CLI, SDK, the Desktop app's Code tab) appends to
`<claude_home>/projects/<slug>/<session-id>.jsonl`, and every running client registers in
`<claude_home>/sessions/<pid>.json`. This module reads both. It is the productized form of
`tools/lab/claude_client/session_watch.py`.

The formats are undocumented and can change, so everything here is defensive: unreadable
files, bad JSON and unknown line types are skipped, never raised. Standard library only
(no Qt, nothing from `app.core` or `app.features`) so it also runs on the engine side.

A turn can end with two signals (an assistant line with `stop_reason == "end_turn"` and,
when hooks are configured, a later `stop_hook_summary` line). `parse_line` reports both;
`Tail` collapses the pair into one `turn_end`.
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

MAX_SANITIZED_LENGTH: Final = 200
# The title lives in a line the CLI rewrites every turn: reading this much of the end is enough.
TITLE_TAIL_BYTES: Final = 256_000
_SANITIZE_RE: Final = re.compile(r"[^a-zA-Z0-9]")
_BASE36_DIGITS: Final = "0123456789abcdefghijklmnopqrstuvwxyz"

Role = Literal["user", "assistant"]
EventKind = Literal["user", "assistant_text", "turn_end", "compact", "title", "error"]
SessionStatus = Literal["busy", "idle", "unknown"]


@dataclass(frozen=True)
class Message:
    role: Role
    text: str
    timestamp: str | None = None
    uuid: str | None = None


@dataclass(frozen=True)
class ReaderEvent:
    kind: EventKind
    text: str = ""
    timestamp: str | None = None
    # Identity used to merge the lines of one assistant reply; consumers can ignore both.
    uuid: str | None = None
    message_id: str | None = None


@dataclass(frozen=True)
class LiveSession:
    session_id: str
    pid: int | None
    status: SessionStatus
    name: str = ""


@dataclass(frozen=True)
class ConversationInfo:
    session_id: str
    path: Path
    title: str
    modified: float
    project: str


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


def claude_home() -> Path:
    """The Claude config folder: `CLAUDE_CONFIG_DIR` when set, otherwise `~/.claude`."""
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return Path(override) if override else Path.home() / ".claude"


def _simple_hash(text: str) -> str:
    """Port of the CLI's 32-bit JS string hash, in base36 (suffix for paths over 200 chars)."""
    h = 0
    for ch in text:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    h = abs(h)
    out = ""
    while h:
        h, r = divmod(h, 36)
        out = _BASE36_DIGITS[r] + out
    return out or "0"


def project_slug(cwd: str) -> str:
    """Folder name under `projects/` for a working directory.

    Every non-alphanumeric character becomes `-` (`C:\\a\\.b` -> `C--a--b`). Slugs longer than
    200 characters are cut there and get `-<base36 hash of the original path>`. The drive
    letter keeps the case the client saw, so look the folder up with `project_dir`.
    """
    slug = _SANITIZE_RE.sub("-", cwd)
    if len(slug) <= MAX_SANITIZED_LENGTH:
        return slug
    return f"{slug[:MAX_SANITIZED_LENGTH]}-{_simple_hash(cwd)}"


def _project_folders() -> list[Path]:
    try:
        return [Path(e.path) for e in os.scandir(claude_home() / "projects") if e.is_dir()]
    except OSError:
        return []


def project_dir(cwd: str) -> Path | None:
    """The existing project folder for `cwd` (case-insensitive match), or None."""
    want = project_slug(cwd).lower()
    for folder in _project_folders():
        if folder.name.lower() == want:
            return folder
    return None


def find_session_file(session_id: str) -> Path | None:
    """The transcript of a session id, searched in every project folder."""
    if not session_id or "/" in session_id or "\\" in session_id:
        return None
    for project in _project_folders():
        candidate = project / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _text_blocks(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text", "")) for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_tool_result_only(content: object) -> bool:
    if not isinstance(content, list):
        return False
    has_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    return has_result and not _text_blocks(content)


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def parse_line(raw: str | bytes) -> list[ReaderEvent]:
    """Map one transcript line to zero or more events. Never raises."""
    try:
        d = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(d, dict):
        return []
    kind = d.get("type")
    ts = _as_str(d.get("timestamp"))
    uuid = _as_str(d.get("uuid"))
    if kind == "user":
        if d.get("isMeta") or d.get("isSidechain") or d.get("isCompactSummary"):
            return []
        message = d.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if _is_tool_result_only(content):
            return []
        text = _text_blocks(content)
        return [ReaderEvent("user", text, ts, uuid)] if text else []
    if kind == "assistant":
        message = d.get("message")
        if d.get("isSidechain") or not isinstance(message, dict):
            return []
        mid = _as_str(message.get("id"))
        events: list[ReaderEvent] = []
        # The CLI writes one line per content block; the blocks of one line are joined here and
        # the lines of one reply share message.id (merged by `last_messages`).
        text = _text_blocks(message.get("content"))
        if text:
            events.append(ReaderEvent("assistant_text", text, ts, uuid, mid))
        if message.get("stop_reason") == "end_turn":
            events.append(ReaderEvent("turn_end", "", ts, uuid, mid))
        return events
    if kind == "system":
        subtype = d.get("subtype")
        if subtype == "stop_hook_summary":
            return [ReaderEvent("turn_end", "", ts, uuid)]
        if subtype == "compact_boundary":
            return [ReaderEvent("compact", "", ts, uuid)]
        if subtype == "api_error":
            err = d.get("error")
            formatted = err.get("formatted") if isinstance(err, dict) else None
            return [ReaderEvent("error", str(formatted or ""), ts, uuid)]
        return []
    if kind in ("custom-title", "ai-title"):
        title = _as_str(d.get("customTitle")) or _as_str(d.get("aiTitle")) or ""
        return [ReaderEvent("title", title, ts, uuid)]
    return []


# ---------------------------------------------------------------------------
# Following a growing file
# ---------------------------------------------------------------------------


class Tail:
    """Incremental reader: keeps a byte offset and holds back an unfinished last line.

    `poll()` returns the events that appeared since the last call, each exactly once. If the
    file shrinks (rewritten or truncated) it starts over from the beginning. Repeated titles
    (the CLI rewrites the title line every turn) and the second signal of one turn end are
    dropped.
    """

    def __init__(self, path: str | Path, *, from_start: bool = False) -> None:
        self.path = Path(path)
        self.offset = 0 if from_start else self._size()
        self._partial = b""
        self._last_title: str | None = None
        self._turn_open = False

    def _size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def poll(self) -> list[ReaderEvent]:
        size = self._size()
        if size < self.offset:
            self.offset, self._partial = 0, b""
            self._last_title, self._turn_open = None, False
        if size == self.offset:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(size - self.offset)
        except OSError:
            return []
        self.offset += len(chunk)
        lines = (self._partial + chunk).split(b"\n")
        self._partial = lines.pop()  # empty when the chunk ended with a newline
        out: list[ReaderEvent] = []
        for line in lines:
            if line.strip():
                out.extend(self._accept(parse_line(line)))
        return out

    def _accept(self, events: list[ReaderEvent]) -> Iterator[ReaderEvent]:
        for ev in events:
            if ev.kind == "title":
                if ev.text == self._last_title:
                    continue
                self._last_title = ev.text
            elif ev.kind == "turn_end":
                if not self._turn_open:
                    continue
                self._turn_open = False
            elif ev.kind in ("user", "assistant_text"):
                self._turn_open = True
            yield ev


# ---------------------------------------------------------------------------
# Reading history
# ---------------------------------------------------------------------------


def last_messages(path: str | Path, n: int) -> list[Message]:
    """The last `n` user and assistant messages, counting only what follows the last compaction.

    The lines of one assistant reply (same message id, adjacent) are joined into one message.
    """
    if n <= 0:
        return []
    kept: deque[Message] = deque(maxlen=n)
    last_mid: str | None = None
    try:
        with open(path, "rb") as f:
            for line in f:
                for ev in parse_line(line):
                    if ev.kind == "compact":
                        kept.clear()
                        last_mid = None
                    elif ev.kind == "user":
                        kept.append(Message("user", ev.text, ev.timestamp, ev.uuid))
                        last_mid = None
                    elif ev.kind == "assistant_text":
                        same_reply = bool(kept) and ev.message_id is not None and ev.message_id == last_mid
                        if same_reply and kept[-1].role == "assistant":
                            kept[-1] = replace(kept[-1], text=kept[-1].text + ev.text)
                        else:
                            kept.append(Message("assistant", ev.text, ev.timestamp, ev.uuid))
                        last_mid = ev.message_id
    except OSError:
        return []
    return list(kept)


# ---------------------------------------------------------------------------
# Live sessions and the conversation list
# ---------------------------------------------------------------------------


def _status(value: object) -> SessionStatus:
    if value == "busy":
        return "busy"
    return "idle" if value == "idle" else "unknown"


def live_sessions() -> dict[str, LiveSession]:
    """Running clients by session id, from `<claude_home>/sessions/<pid>.json`.

    Only the `.json` registry files are read (the `.key` files next to them are never
    opened); unreadable or malformed files are ignored.
    """
    out: dict[str, LiveSession] = {}
    try:
        entries = sorted(e.path for e in os.scandir(claude_home() / "sessions") if e.name.endswith(".json"))
    except OSError:
        return out
    for path in entries:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        sid = d.get("sessionId") if isinstance(d, dict) else None
        if not isinstance(sid, str) or not sid:
            continue
        pid = d.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            stem = Path(path).stem
            pid = int(stem) if stem.isdigit() else None
        name = d.get("name")
        out[sid] = LiveSession(sid, pid, _status(d.get("status")), name if isinstance(name, str) else "")
    return out


def _session_title(path: Path) -> str:
    """Last custom title (else last AI title) of a transcript; reads only the end of big files."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TITLE_TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    ai = ""
    for line in reversed(tail):
        is_custom = '"custom-title"' in line
        if not is_custom and '"ai-title"' not in line:
            continue
        for ev in parse_line(line):
            if ev.kind != "title" or not ev.text:
                continue
            if is_custom:
                return ev.text
            ai = ai or ev.text
    return ai


def list_conversations_raw(limit: int) -> list[ConversationInfo]:
    """Like `list_conversations`, without reading the titles (`title` is empty)."""
    found: list[tuple[float, Path]] = []
    for project in _project_folders():
        try:
            for entry in os.scandir(project):
                if entry.name.endswith(".jsonl") and entry.is_file():
                    found.append((entry.stat().st_mtime, Path(entry.path)))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [ConversationInfo(p.stem, p, "", mtime, p.parent.name) for mtime, p in found[: max(0, limit)]]


def list_conversations(limit: int = 15) -> list[ConversationInfo]:
    """The most recently modified transcripts across all projects, newest first."""
    return [replace(info, title=_session_title(info.path)) for info in list_conversations_raw(limit)]


def _custom_title(path: Path) -> str:
    """Last custom title of a transcript ("" when it has none; the AI title does not count)."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            f.seek(max(0, size - TITLE_TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(tail):
        if '"custom-title"' not in line:
            continue
        for ev in parse_line(line):
            if ev.kind == "title" and ev.text:
                return ev.text
    return ""


def find_sessions_by_title(title: str, limit: int = 2000) -> list[ConversationInfo]:
    """Transcripts whose current custom title is exactly `title`, newest first.

    The Claude Desktop writes the name of a conversation as `custom-title` lines of its transcript
    (the title shown in the sidebar). Looks at the `limit` most recently modified files and opens
    only those whose tail mentions the title.
    """
    wanted = title.strip()
    if not wanted:
        return []
    # The file holds the title raw (Node writers) or escaped (Python writers).
    needles = (json.dumps(wanted, ensure_ascii=False)[1:-1], json.dumps(wanted)[1:-1])
    matches: list[ConversationInfo] = []
    for info in list_conversations_raw(limit):
        try:
            with open(info.path, "rb") as f:
                f.seek(max(0, info.path.stat().st_size - TITLE_TAIL_BYTES))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        if not any(needle in tail for needle in needles):
            continue
        if _custom_title(info.path) == wanted:
            matches.append(ConversationInfo(info.session_id, info.path, wanted, info.modified, info.project))
    return matches
