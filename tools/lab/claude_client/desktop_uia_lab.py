"""Windows UI Automation lab: read and drive the Claude Desktop window like a screen reader.

Research prototype, not production code. Needs `pip install uiautomation` (Windows only).

  python desktop_uia_lab.py dump [--depth 40]   print the accessibility tree of the Claude window
  python desktop_uia_lab.py prompt              find the prompt box and print what it holds
  python desktop_uia_lab.py type "text"         put text in the prompt box without sending it
  python desktop_uia_lab.py send "text"         put text in the prompt box and press Enter
  python desktop_uia_lab.py read [--last 5]     print the last messages shown in the conversation

`type`/`send` act on whatever conversation is open in the window; open the right one
first (for example with the deep link `claude://claude.ai/epitaxy/local_<id>`).
"""

from __future__ import annotations

import argparse
import sys
import time

import uiautomation as auto


def claude_window() -> auto.WindowControl:
    for w in auto.GetRootControl().GetChildren():
        if w.ControlTypeName == "WindowControl" and w.ClassName.startswith("Chrome_WidgetWin") and "Claude" in w.Name:
            return w
    sys.exit("Claude Desktop window not found")


def walk(ctrl: auto.Control, depth: int = 0, max_depth: int = 40):  # noqa: ANN201
    yield ctrl, depth
    if depth >= max_depth:
        return
    for child in ctrl.GetChildren():
        yield from walk(child, depth + 1, max_depth)


def find_prompt(win: auto.Control) -> auto.Control | None:
    """The Code tab prompt is a ProseMirror (tiptap) editor exposed as an Edit control named "Prompt"."""
    for c, _ in walk(win, max_depth=90):
        if c.ControlTypeName == "EditControl" and "ProseMirror" in c.ClassName:
            return c
    return None


def prompt_text(c: auto.Control) -> str:
    """ProseMirror exposes no Value; the text lives in the Text children of the editor."""
    return "".join(t.Name for t, _ in walk(c) if t.ControlTypeName == "TextControl").strip()


def conversation_title(win: auto.Control) -> str:
    for c, _ in walk(win, max_depth=30):
        if c.ControlTypeName == "DocumentControl" and c.AutomationId == "RootWebArea" and c.Name:
            return c.Name
    return ""


def set_prompt(win: auto.Control, c: auto.Control, text: str) -> None:
    """Bring the window forward, focus the editor and type. Keystrokes go to the foreground
    window, so without SetActive they can land in whatever app the person is using."""
    win.SetActive()
    c.SetFocus()
    time.sleep(0.2)
    auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.1)
    auto.SendKeys(text.replace("{", "{{}").replace("}", "{}}"), interval=0.005, waitTime=0.2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["dump", "prompt", "type", "send", "read"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--depth", type=int, default=40)
    ap.add_argument("--last", type=int, default=5)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    win = claude_window()
    t0 = time.perf_counter()
    if args.cmd == "dump":
        for c, d in walk(win, max_depth=args.depth):
            name = (c.Name or "").replace("\n", " ")[:80]
            print(f"{'  ' * d}{c.ControlTypeName} cls={c.ClassName[:30]!r} aid={c.AutomationId[:30]!r} name={name!r}")
    elif args.cmd == "prompt":
        p = find_prompt(win)
        print("conversation:", conversation_title(win))
        print("prompt:", p and p.ClassName, repr(p and prompt_text(p)))
    elif args.cmd in ("type", "send"):
        p = find_prompt(win)
        if p is None:
            sys.exit("prompt box not found")
        print("conversation:", conversation_title(win))
        set_prompt(win, p, args.text or "")
        print(f"box now holds {prompt_text(p)!r}")
        if args.cmd == "send":
            auto.SendKeys("{Enter}", waitTime=0.3)
            print("sent")
    elif args.cmd == "read":
        texts = [c.Name for c, _ in walk(win) if c.ControlTypeName == "TextControl" and c.Name and len(c.Name) > 20]
        for t in texts[-args.last :]:
            print("-", t[:300].replace("\n", " "))
    print(f"# {time.perf_counter() - t0:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
