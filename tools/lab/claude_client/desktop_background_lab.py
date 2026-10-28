"""Background control lab: drive the Claude Desktop while another app keeps the focus.

Research prototype, not production code. Windows only, needs `uiautomation`.
Notepad stands in for "the spreadsheet the person is working in". Steps:

  1. post keystrokes (WM_CHAR) to the Desktop's web view while Notepad is in front
  2. the same with the Desktop minimized
  3. switch conversations by invoking sidebar buttons, in the background
  4. "flash" mode: bring the Desktop forward, type, Enter, give the focus back

Only acts on the conversation titled exactly --lab-title; every send is prefixed with a
marker so the transcript shows where it came from.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import subprocess
import sys
import time

import uiautomation as auto
from uia_common import (
    claude_window,
    conversation_doc,
    ensure_focus_in_prompt,
    ensure_foreground,
    find_prompt,
    prompt_text,
    require_title,
    title_matches,
    walk,
)

user32 = ctypes.windll.user32
WM_CHAR, WM_KEYDOWN, WM_KEYUP, WM_CLOSE, VK_RETURN = 0x0102, 0x0100, 0x0101, 0x0010, 0x0D


def render_hwnd(doc: auto.Control) -> tuple[int, str]:
    """Handle and class of the window that hosts the conversation page (Chrome_RenderWidgetHostHWND)."""
    parent = doc.GetParentControl()
    return parent.NativeWindowHandle, parent.ClassName


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


def notepad_handles() -> dict[int, int]:
    """Top-level Notepad windows as {hwnd: pid}."""
    return {w.NativeWindowHandle: w.ProcessId for w in auto.GetRootControl().GetChildren() if w.ClassName == "Notepad"}


def launch_notepad(known: set[int]) -> tuple[subprocess.Popen, int]:
    """Start Notepad and return (process, hwnd of its window), or abort.

    On Windows 11 notepad.exe may hand over to another process, so the window can belong to
    a pid other than the launched one; then accept the Notepad window that did not exist before."""
    note = subprocess.Popen(["notepad.exe"])
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        found = notepad_handles()
        for hwnd, pid in found.items():
            if pid == note.pid:
                return note, hwnd
        new = [h for h in found if h not in known]
        if new:
            return note, new[0]
        time.sleep(0.2)
    note.kill()
    sys.exit("Notepad window not found")


def sidebar_button(win: auto.Control, title_prefix: str) -> auto.Control | None:
    for c, _ in walk(win, max_depth=40):
        if c.ControlTypeName == "ButtonControl" and title_matches(c.Name, title_prefix) and "Mais opções" not in c.Name:
            return c
    return None


def doc_name(win: auto.Control) -> str:
    doc = conversation_doc(win)
    return doc.Name if doc else ""


def switch_to(win: auto.Control, button: auto.Control, timeout: float = 5.0) -> float | None:
    """Invoke a sidebar button; seconds until the document name changes (None on timeout)."""
    before = doc_name(win)
    t0 = time.perf_counter()
    button.GetInvokePattern().Invoke()
    while time.perf_counter() - t0 < timeout:
        if doc_name(win) != before:
            return time.perf_counter() - t0
        time.sleep(0.05)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab-title", required=True, help="title of the conversation the lab may type into")
    ap.add_argument("--other-title", required=True, help="a conversation to switch to and back (view only)")
    ap.add_argument("--send", action="store_true", help="also press Enter (sends into the lab conversation)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

    win = claude_window()
    note, other_app = launch_notepad(set(notepad_handles()))
    try:
        run(win, other_app, args)
    finally:
        # The run may stop with the Desktop minimized, and the Notepad window may not belong to the
        # launched pid, so restore the Desktop and close Notepad by window handle.
        with contextlib.suppress(Exception):
            win.GetWindowPattern().SetWindowVisualState(auto.WindowVisualState.Normal)
        user32.PostMessageW(other_app, WM_CLOSE, 0, 0)
        note.kill()


def run(win: auto.Control, other_app: int, args: argparse.Namespace) -> None:
    user32.SetForegroundWindow(other_app)
    time.sleep(0.5)
    print("foreground (stand-in for Excel):", fg_title())

    require_title(win, args.lab_title)
    doc = conversation_doc(win)
    p = find_prompt(win)
    assert doc is not None and p is not None
    hwnd, hwnd_class = render_hwnd(doc)
    print(f"render window: hwnd={hwnd:#x} class={hwnd_class!r}")
    if not hwnd:
        sys.exit("render window handle is 0, the posted keys would not reach the page")

    # 1. background, window visible but not in front
    require_title(win, args.lab_title)
    post_text(hwnd, "VOICEMATE BG LAB 1 (background) ", enter=False)
    time.sleep(0.8)
    print("1. prompt now:", repr(prompt_text(p)), "| foreground:", fg_title())

    # 2. minimized
    win.GetWindowPattern().SetWindowVisualState(auto.WindowVisualState.Minimized)
    time.sleep(1.0)
    require_title(win, args.lab_title)
    post_text(hwnd, "+ 2 (minimized)", enter=args.send)
    time.sleep(1.5)
    p = find_prompt(win)
    print(
        "2. tree readable while minimized:",
        p is not None,
        "| prompt:",
        repr(p and prompt_text(p)),
        "| foreground:",
        fg_title(),
    )

    # 3. switch conversations while minimized, then back; time from Invoke() to the new document name
    other = sidebar_button(win, args.other_title)
    if other is not None:
        took = switch_to(win, other)
        print("3. after invoke:", doc_name(win), f"({'timeout' if took is None else f'{took:.2f}s'})")
        back = sidebar_button(win, args.lab_title)
        if back is not None:
            took = switch_to(win, back)
            print("3. back on:", doc_name(win), f"({'timeout' if took is None else f'{took:.2f}s'})")
            # A label like "<title> - notes" also matches; say so instead of trusting it.
            if doc_name(win) != f"{args.lab_title} - Claude Code":
                print("3. warning: the sidebar match opened another conversation")
        else:
            print("3. sidebar button for the lab conversation not found")
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
    try:
        win.SetActive()
        p = find_prompt(win)
        if p is None:
            sys.exit("prompt box not found")
        p.SetFocus()
        time.sleep(0.2)
        # Re-check right before typing: a conversation switch changes where the keys land.
        ensure_foreground(win)
        require_title(win, args.lab_title)
        ensure_focus_in_prompt()
        auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.05)
        auto.SendKeys("VOICEMATE BG LAB 4 (flash)", interval=0.002, waitTime=0.05)
        if args.send:
            ensure_foreground(win)
            require_title(win, args.lab_title)
            ensure_focus_in_prompt()
            auto.SendKeys("{Enter}", waitTime=0.05)
    finally:
        user32.SetForegroundWindow(before)
    print(f"4. flash took {time.perf_counter() - t0:.2f}s, foreground back to: {fg_title()}")

    time.sleep(1.0)


if __name__ == "__main__":
    main()
