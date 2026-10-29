"""Windows UI Automation lab: read and drive the Claude Desktop window like a screen reader.

Research prototype, not production code. Needs `pip install uiautomation` (Windows only).

  python desktop_uia_lab.py dump [--depth 40]   print the accessibility tree of the Claude window
  python desktop_uia_lab.py prompt              find the prompt box and print what it holds
  python desktop_uia_lab.py type "text" --expect-title T   put text in the prompt box without sending it
  python desktop_uia_lab.py send "text" --expect-title T   put text in the prompt box and press Enter
  python desktop_uia_lab.py read [--last 5]     print the last messages shown in the conversation

`type`/`send` act on the conversation open in the window, and only when its title starts
is exactly --expect-title (checked right before the keys and again before Enter); open the
right one first (for example with the deep link `claude://claude.ai/epitaxy/local_<id>`).
"""

from __future__ import annotations

import argparse
import sys
import time

import uiautomation as auto
from uia_common import (
    claude_window,
    conversation_title,
    ensure_focus_in_prompt,
    ensure_foreground,
    escape_sendkeys,
    find_prompt,
    prompt_text,
    require_title,
    walk,
)


def set_prompt(win: auto.Control, c: auto.Control, text: str, expected_title: str) -> None:
    """Bring the window forward, focus the editor and type. Keystrokes go to the foreground
    window, so SetActive is not enough: confirm the foreground, the conversation and the
    focused element right before the first key."""
    win.SetActive()
    c.SetFocus()
    time.sleep(0.2)
    ensure_foreground(win)
    require_title(win, expected_title)
    ensure_focus_in_prompt()
    auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.1)
    auto.SendKeys(escape_sendkeys(text), interval=0.005, waitTime=0.2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["dump", "prompt", "type", "send", "read"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--depth", type=int, default=40)
    ap.add_argument("--last", type=int, default=5)
    ap.add_argument("--expect-title", help="required by type/send: exact title of the conversation to write to")
    args = ap.parse_args()
    if args.cmd in ("type", "send") and not args.expect_title:
        ap.error("type and send require --expect-title")
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
        require_title(win, args.expect_title)
        set_prompt(win, p, args.text or "", args.expect_title)
        print(f"box now holds {prompt_text(p)!r}")
        if args.cmd == "send":
            # The conversation may have changed while typing: check again before Enter.
            ensure_foreground(win)
            require_title(win, args.expect_title)
            ensure_focus_in_prompt()
            auto.SendKeys("{Enter}", waitTime=0.3)
            print("sent")
    elif args.cmd == "read":
        texts = [c.Name for c, _ in walk(win) if c.ControlTypeName == "TextControl" and c.Name and len(c.Name) > 20]
        for t in texts[-args.last :]:
            print("-", t[:300].replace("\n", " "))
    print(f"# {time.perf_counter() - t0:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
