"""Background control lab: drive the Claude Desktop while another app keeps the focus.

Research prototype, not production code. Windows only, needs `uiautomation`.
Notepad stands in for "the spreadsheet the person is working in". Steps:

  1. post keystrokes (WM_CHAR) to the Desktop's web view while Notepad is in front
  2. the same with the Desktop minimized
  3. switch conversations by invoking sidebar buttons, in the background
  4. "flash" mode: bring the Desktop forward, type, Enter, give the focus back

Only acts on the conversation named by --lab-title; every send is prefixed with a
marker so the transcript shows where it came from.
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from collections.abc import Iterator

import uiautomation as auto

user32 = ctypes.windll.user32
WM_CHAR, WM_KEYDOWN, WM_KEYUP, VK_RETURN = 0x0102, 0x0100, 0x0101, 0x0D


def walk(c: auto.Control, d: int = 0, max_depth: int = 90) -> Iterator[auto.Control]:
    yield c
    if d < max_depth:
        for ch in c.GetChildren():
            yield from walk(ch, d + 1, max_depth)


def claude_window() -> auto.Control:
    for w in auto.GetRootControl().GetChildren():
        if w.ClassName.startswith("Chrome_WidgetWin") and w.Name == "Claude":
            return w
    sys.exit("Claude window not found")


def conversation_doc(win: auto.Control) -> auto.Control | None:
    for c in walk(win, max_depth=30):
        if (
            c.ControlTypeName == "DocumentControl"
            and c.AutomationId == "RootWebArea"
            and c.Name.endswith("Claude Code")
        ):
            return c
    return None


def render_hwnd(doc: auto.Control) -> int:
    """The Chrome_RenderWidgetHostHWND that hosts the conversation page."""
    parent = doc.GetParentControl()
    return parent.NativeWindowHandle


def prompt(win: auto.Control) -> auto.Control | None:
    for c in walk(win):
        if c.ControlTypeName == "EditControl" and "ProseMirror" in c.ClassName:
            return c
    return None


def prompt_text(p: auto.Control) -> str:
    return "".join(t.Name for t in walk(p) if t.ControlTypeName == "TextControl").strip()


def post_text(hwnd: int, text: str, enter: bool) -> None:
    for ch in text:
        user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
    if enter:
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0x001C0001)
        user32.PostMessageW(hwnd, WM_CHAR, VK_RETURN, 0x001C0001)
        user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0xC01C0001)


def fg_title() -> str:
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def sidebar_button(win: auto.Control, title_prefix: str) -> auto.Control | None:
    for c in walk(win, max_depth=40):
        if c.ControlTypeName == "ButtonControl" and c.Name.startswith(title_prefix) and "Mais opções" not in c.Name:
            return c
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab-title", required=True, help="title of the conversation the lab may type into")
    ap.add_argument("--other-title", required=True, help="a conversation to switch to and back (view only)")
    ap.add_argument("--send", action="store_true", help="also press Enter (sends into the lab conversation)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    win = claude_window()
    note = subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)
    other_app = user32.GetForegroundWindow()
    print("foreground (stand-in for Excel):", fg_title())

    doc = conversation_doc(win)
    assert doc is not None and doc.Name.startswith(args.lab_title), f"open conversation is {doc and doc.Name!r}"
    p = prompt(win)
    assert p is not None
    hwnd = render_hwnd(doc)

    # 1. background, window visible but not in front
    post_text(hwnd, "VOICEMATE BG LAB 1 (background) ", enter=False)
    time.sleep(0.8)
    print("1. prompt now:", repr(prompt_text(p)), "| foreground:", fg_title())

    # 2. minimized
    win.GetWindowPattern().SetWindowVisualState(auto.WindowVisualState.Minimized)
    time.sleep(1.0)
    post_text(hwnd, "+ 2 (minimized)", enter=args.send)
    time.sleep(1.5)
    p = prompt(win)
    print(
        "2. tree readable while minimized:",
        p is not None,
        "| prompt:",
        repr(p and prompt_text(p)),
        "| foreground:",
        fg_title(),
    )

    # 3. switch conversations while minimized, then back
    t0 = time.perf_counter()
    other = sidebar_button(win, args.other_title)
    if other is not None:
        other.GetInvokePattern().Invoke()
        time.sleep(2.5)  # the document name updates after the view switches
        print("3. after invoke:", conversation_doc(win) and conversation_doc(win).Name)
        back = sidebar_button(win, args.lab_title)
        if back is not None:
            back.GetInvokePattern().Invoke()
            time.sleep(1.5)
        print("3. back on:", conversation_doc(win) and conversation_doc(win).Name, f"({time.perf_counter() - t0:.1f}s)")
    else:
        print("3. sidebar button not found (sidebar hidden while minimized?)")

    win.GetWindowPattern().SetWindowVisualState(auto.WindowVisualState.Normal)
    time.sleep(1.0)

    # 4. flash mode: in, type, Enter, back
    user32.SetForegroundWindow(other_app)
    time.sleep(0.5)
    before = user32.GetForegroundWindow()
    print("4. before flash, foreground:", fg_title())
    t0 = time.perf_counter()
    win.SetActive()
    p = prompt(win)
    doc = conversation_doc(win)
    # Re-check right before typing: a conversation switch changes where the keys land.
    if p is not None and doc is not None and doc.Name.startswith(args.lab_title):
        p.SetFocus()
        auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.05)
        auto.SendKeys("VOICEMATE BG LAB 4 (flash)", interval=0.002, waitTime=0.05)
        if args.send:
            auto.SendKeys("{Enter}", waitTime=0.05)
    user32.SetForegroundWindow(before)
    print(f"4. flash took {time.perf_counter() - t0:.2f}s, foreground back to: {fg_title()}")

    time.sleep(1.0)
    note.kill()


if __name__ == "__main__":
    main()
