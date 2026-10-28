"""Disk reader lab: follow Claude Code sessions without spawning the CLI or the SDK.

Research prototype, not production code. Reads the session transcripts that every
Claude Code client (CLI, SDK, the Desktop app's Code tab) appends to
`~/.claude/projects/<slug>/<session-id>.jsonl`, plus the live-process registry in
`~/.claude/sessions/<pid>.json`, and prints events as they appear.

Standard library only, so it runs on the Windows companion side and inside WSL.

Usage:
  python tools/lab/claude_client/session_watch.py list [--limit 15]
  python tools/lab/claude_client/session_watch.py follow <session-id> [--from-start]
  python tools/lab/claude_client/session_watch.py follow --cwd <project dir>   (latest session of that dir)
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

MAX_SANITIZED_LENGTH = 200
_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9]")


def claude_home() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def _simple_hash(s: str) -> str:
    """Port of the CLI's 32-bit JS string hash, base36 (used for paths over 200 chars)."""
    h = 0
    for ch in s:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
    if h >= 0x80000000:
        h -= 0x100000000
    h = abs(h)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while h:
        h, r = divmod(h, 36)
        out = digits[r] + out
    return out or "0"


def project_slug(cwd: str) -> str:
    """Folder name under ~/.claude/projects for a working directory.

    Every non-alphanumeric character becomes `-` (`C:\\a\\.b` -> `C--a--b`). Long paths are
    cut at 200 chars plus a hash. The drive letter keeps whatever case the client saw,
    so on Windows look the folder up case-insensitively (see `project_dir`).
    """
    s = _SANITIZE_RE.sub("-", cwd)
    return s if len(s) <= MAX_SANITIZED_LENGTH else f"{s[:MAX_SANITIZED_LENGTH]}-{_simple_hash(cwd)}"


def project_dir(cwd: str) -> str | None:
    root = os.path.join(claude_home(), "projects")
    want = project_slug(cwd).lower()
    for name in os.listdir(root):
        if name.lower() == want:
            return os.path.join(root, name)
    return None


def find_session_file(session_id: str) -> str | None:
    hits = glob.glob(os.path.join(claude_home(), "projects", "*", f"{session_id}.jsonl"))
    return hits[0] if hits else None


def live_sessions() -> dict[str, dict]:
    """sessionId -> entry of ~/.claude/sessions/<pid>.json (running clients: status busy/idle, name)."""
    out: dict[str, dict] = {}
    for path in glob.glob(os.path.join(claude_home(), "sessions", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("sessionId"):
            out[d["sessionId"]] = d
    return out


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------


@dataclass
class Event:
    kind: str  # user | assistant_text | tool_use | tool_result | turn_end | compact | title | queued | error
    session_id: str
    text: str = ""
    uuid: str | None = None
    timestamp: str | None = None
    extra: dict = field(default_factory=dict)


def _text_blocks(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse_line(d: dict) -> Iterator[Event]:
    """Map one transcript line to zero or more events."""
    t = d.get("type")
    sid = d.get("sessionId", "")
    base = {"session_id": sid, "uuid": d.get("uuid"), "timestamp": d.get("timestamp")}
    if t == "user" and not d.get("isMeta") and not d.get("isSidechain"):
        content = d.get("message", {}).get("content")
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            yield Event("tool_result", **base)
        elif d.get("isCompactSummary"):
            yield Event("compact", text="(compact summary)", **base)
        else:
            yield Event(
                "user",
                text=_text_blocks(content),
                extra={"entrypoint": d.get("entrypoint"), "promptSource": d.get("promptSource")},
                **base,
            )
    elif t == "assistant" and not d.get("isSidechain"):
        msg = d.get("message", {})
        # The CLI writes one line per content block; all lines of one API reply share message.id.
        for block in msg.get("content", []):
            if block.get("type") == "text" and block.get("text"):
                yield Event("assistant_text", text=block["text"], extra={"message_id": msg.get("id")}, **base)
            elif block.get("type") == "tool_use":
                yield Event("tool_use", text=block.get("name", ""), **base)
        if msg.get("stop_reason") == "end_turn":
            yield Event("turn_end", extra={"via": "stop_reason"}, **base)
    elif t == "system":
        st = d.get("subtype")
        if st == "compact_boundary":
            yield Event("compact", text="compact boundary", **base)
        elif st == "api_error":
            yield Event("error", text=str(d.get("error", {}).get("formatted", "")), **base)
    elif t in ("custom-title", "ai-title"):
        yield Event("title", text=d.get("customTitle") or d.get("aiTitle") or "", session_id=sid)
    elif t == "queue-operation" and d.get("operation") == "enqueue":
        # A prompt typed while the session was busy (Desktop, phone or another writer).
        yield Event("queued", text=str(d.get("content", ""))[:200], session_id=sid, timestamp=d.get("timestamp"))


class Tail:
    """Incremental reader: remembers the byte offset and keeps an unfinished last line for later."""

    def __init__(self, path: str, from_start: bool = False) -> None:
        self.path = path
        self.offset = 0 if from_start else os.path.getsize(path)
        self._partial = b""

    def read_new(self) -> list[dict]:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:  # rewritten or truncated: start over
            self.offset, self._partial = 0, b""
        if size == self.offset:
            return []
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read(size - self.offset)
        self.offset = size
        data = self._partial + chunk
        lines = data.split(b"\n")
        self._partial = lines.pop()  # empty when the chunk ended with a newline
        out = []
        for raw in lines:
            if raw.strip():
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
        return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _session_title(path: str) -> str:
    """Last custom/ai title in the file; reads only the tail to stay cheap on big transcripts."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - 256_000))
        tail = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(tail):
        if '"custom-title"' in line or '"ai-title"' in line:
            try:
                d = json.loads(line)
                return d.get("customTitle") or d.get("aiTitle") or ""
            except json.JSONDecodeError:
                continue
    return ""


def cmd_list(args: argparse.Namespace) -> None:
    files = glob.glob(os.path.join(claude_home(), "projects", "*", "*.jsonl"))
    files.sort(key=os.path.getmtime, reverse=True)
    live = live_sessions()
    for path in files[: args.limit]:
        sid = os.path.basename(path)[:-6]
        status = live.get(sid, {}).get("status", "-")
        age = time.time() - os.path.getmtime(path)
        print(
            f"{sid}  {status:5s}  {age / 60:6.1f} min ago  {_session_title(path)[:50]!s:50s}  "
            f"{os.path.basename(os.path.dirname(path))[:60]}"
        )


def cmd_follow(args: argparse.Namespace) -> None:
    if args.session:
        path = find_session_file(args.session)
    else:
        pdir = project_dir(os.path.abspath(args.cwd))
        files = glob.glob(os.path.join(pdir, "*.jsonl")) if pdir else []
        path = max(files, key=os.path.getmtime) if files else None
    if not path:
        sys.exit("session file not found")
    sid = os.path.basename(path)[:-6]
    print(f"following {path}", flush=True)
    tail = Tail(path, from_start=args.from_start)
    last_status = None
    last_title = None
    while True:
        for d in tail.read_new():
            for ev in parse_line(d):
                if ev.kind == "title":  # the CLI rewrites the title line every turn
                    if ev.text == last_title:
                        continue
                    last_title = ev.text
                if ev.kind in args.kinds:
                    stamp = (ev.timestamp or "")[11:19]
                    print(f"[{stamp}] {ev.kind:14s} {ev.text[: args.width]!r}", flush=True)
        status = live_sessions().get(sid, {}).get("status")
        if status != last_status and "status" in args.kinds:
            print(f"[{time.strftime('%H:%M:%S')}] status         {status}", flush=True)
            last_status = status
        if args.once:
            return
        time.sleep(args.interval)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--limit", type=int, default=15)
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("follow")
    p.add_argument("session", nargs="?")
    p.add_argument("--cwd", default=os.getcwd())
    p.add_argument("--from-start", action="store_true")
    p.add_argument("--once", action="store_true", help="read what is there and exit")
    p.add_argument("--interval", type=float, default=0.25)
    p.add_argument("--width", type=int, default=160)
    p.add_argument(
        "--kinds",
        nargs="*",
        default=["user", "assistant_text", "tool_use", "turn_end", "compact", "queued", "error", "status"],
    )
    p.set_defaults(fn=cmd_follow)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # Windows consoles default to cp1252
    args.fn(args)


if __name__ == "__main__":
    main()
