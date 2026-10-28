"""Shared Windows UI Automation helpers for the Claude Desktop labs.

Research prototype, not production code. Windows only, needs `uiautomation`.
Every lab that types into the Desktop goes through the same two guards:
`ensure_foreground` (the keys will land in the Claude window) and `require_title`
(the open conversation is the one the lab may write to).
"""

from __future__ import annotations

import ctypes
import re
import sys
import time
from collections.abc import Iterator

import uiautomation as auto

user32 = ctypes.windll.user32
GA_ROOT = 2


def walk(ctrl: auto.Control, depth: int = 0, max_depth: int = 40) -> Iterator[tuple[auto.Control, int]]:
    yield ctrl, depth
    if depth >= max_depth:
        return
    for child in ctrl.GetChildren():
        yield from walk(child, depth + 1, max_depth)


def claude_window() -> auto.Control:
    """The Desktop window: exact name, so Chrome or Edge on claude.ai never matches."""
    for w in auto.GetRootControl().GetChildren():
        if w.ControlTypeName == "WindowControl" and w.ClassName.startswith("Chrome_WidgetWin") and w.Name == "Claude":
            return w
    sys.exit("Claude Desktop window not found")


def conversation_doc(win: auto.Control) -> auto.Control | None:
    """The open conversation: a Document named `<title> - Claude Code`."""
    for c, _ in walk(win, max_depth=30):
        if (
            c.ControlTypeName == "DocumentControl"
            and c.AutomationId == "RootWebArea"
            and c.Name.endswith("Claude Code")
        ):
            return c
    return None


def conversation_title(win: auto.Control) -> str:
    doc = conversation_doc(win)
    return doc.Name if doc else ""


def find_prompt(win: auto.Control) -> auto.Control | None:
    """The Code tab prompt is a ProseMirror (tiptap) editor exposed as an Edit control named "Prompt"."""
    for c, _ in walk(win, max_depth=90):
        if c.ControlTypeName == "EditControl" and "ProseMirror" in c.ClassName:
            return c
    return None


def prompt_text(c: auto.Control) -> str:
    """ProseMirror exposes no Value; the text lives in the Text children of the editor."""
    return "".join(t.Name for t, _ in walk(c) if t.ControlTypeName == "TextControl").strip()


def escape_sendkeys(text: str) -> str:
    """Escape braces for SendKeys in one pass (chained replaces would re-escape their own output)."""
    return re.sub(r"[{}]", lambda m: "{{}" if m.group() == "{" else "{}}", text)


def ensure_foreground(win: auto.Control, timeout: float = 1.0) -> None:
    """Abort unless the Claude window owns the foreground: SetActive can be refused by Windows,
    and keystrokes sent anyway would land in (and Ctrl+A, Delete would wipe) another app."""
    want = user32.GetAncestor(win.NativeWindowHandle, GA_ROOT)
    deadline = time.monotonic() + timeout
    while True:
        if user32.GetAncestor(user32.GetForegroundWindow(), GA_ROOT) == want:
            return
        if time.monotonic() >= deadline:
            sys.exit("Claude window is not in the foreground, refusing to send keys")
        time.sleep(0.05)


def require_title(win: auto.Control, expected_title: str) -> None:
    """Abort unless the open conversation is exactly `expected_title` (the document is named `<title> - Claude Code`)."""
    name = conversation_title(win)
    if not expected_title or name != f"{expected_title} - Claude Code":
        sys.exit(f"open conversation is {name!r}, expected exactly {expected_title!r}")


def ensure_focus_in_prompt() -> None:
    """Abort unless the keyboard focus is in the prompt editor: the window and the title can be right
    while the focus sits in another pane (a terminal, say), which would receive Ctrl+A, Delete and Enter."""
    focused = auto.GetFocusedControl()
    if focused is None or "ProseMirror" not in focused.ClassName:
        sys.exit("keyboard focus is not in the prompt box, refusing to send keys")


def title_matches(name: str, title: str) -> bool:
    """A sidebar label is the title alone or the title plus a state suffix that starts with a separator."""
    if name == title:
        return True
    rest = name[len(title) :] if name.startswith(title) else ""
    return len(rest) > 1 and rest[0] == " " and not rest[1].isalnum()
