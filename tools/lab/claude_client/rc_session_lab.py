"""Remote Control lab: a session VoiceMate owns, visible and writable in the Desktop and on the phone.

Research prototype, not production code. Starts one `stream-json` CLI (the transport the
Agent SDK uses), sends a turn, turns Remote Control on with the undocumented
`remote_control` control request, sends a second turn and keeps the process alive so the
session can be opened in the Desktop sidebar or on claude.ai/code. Every line the CLI
prints is echoed (stream deltas skipped), so prompts typed in the Desktop or on the phone
show up here with their answers.

With Remote Control on, the session also gets the account connectors despite `--tools ""`,
so use a throwaway project folder and do not send prompts that could trigger them.

Usage (venv with claude-agent-sdk, so the bundled CLI is found):
  python tools/lab/claude_client/rc_session_lab.py --cwd DIR [--name TITLE] [--keep 600]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

from bench_latency import _cli
from persistent_client import scrub_host_env


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True, help="throwaway project folder for the test session")
    ap.add_argument("--name", default="VoiceMate RC lab", help="session title shown in the Desktop")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--keep", type=int, default=600, help="seconds to stay alive after the second turn")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    scrub_host_env()
    os.makedirs(args.cwd, exist_ok=True)

    cmd = [_cli(), "-p", "--model", args.model, "--input-format", "stream-json", "--output-format", "stream-json"]
    cmd += ["--verbose", "--name", args.name, "--tools", ""]
    proc = subprocess.Popen(
        cmd,
        cwd=args.cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdin is not None and proc.stdout is not None
    stdin, stdout = proc.stdin, proc.stdout

    def echo() -> None:
        for line in stdout:
            if '"stream_event"' not in line:
                print("<<", line.strip()[:400], flush=True)

    threading.Thread(target=echo, daemon=True).start()

    def send(obj: dict[str, object]) -> None:
        stdin.write(json.dumps(obj) + "\n")
        stdin.flush()

    def user(text: str) -> None:
        send({"type": "user", "message": {"role": "user", "content": text}})

    def wait(seconds: float) -> None:
        """Sleep, but stop early when the CLI exits; then give up instead of writing to a dead pipe."""
        end = time.monotonic() + seconds
        while time.monotonic() < end and proc.poll() is None:
            time.sleep(0.5)
        if proc.poll() is not None:
            sys.exit(f"CLI exited early with code {proc.returncode}")

    try:
        user("Reply only: lab RC test session.")
        wait(8)
        # The answer carries session_url (the claude.ai/code link the phone lists) and
        # bridge_session_id; bridge_state events follow (ready, then connected).
        send(
            {"type": "control_request", "request_id": "rc1", "request": {"subtype": "remote_control", "enabled": True}}
        )
        wait(15)
        user("Reply only: second turn.")
        wait(args.keep)
    finally:
        stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
