"""Resume, fork and two-writer lab on throwaway sessions.

Research prototype, not production code. Every session it touches is created here,
in the lab directory given by --cwd; it never opens an existing session.

Steps:
  1. writer A (persistent ClaudeSDKClient) creates a session and answers one turn
  2. writer B (`claude -p --resume`, a second process) adds a turn while A stays open
  3. A sends another turn: does A see B's turn?
  4. both writers send a turn at the same time
  5. a fresh resume reads the file: which branch does it continue?
  6. fork_session: new id, same history?
Then it dumps the transcript tree (uuid / parentUuid) to show branching.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

from bench_latency import _cli
from persistent_client import scrub_host_env
from session_watch import find_session_file

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient


async def ask(client: ClaudeSDKClient, prompt: str) -> tuple[str, str]:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    await client.query(prompt)
    text, sid = "", ""
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        elif isinstance(msg, ResultMessage):
            sid = msg.session_id
    return text.strip(), sid


def cli_turn(args: argparse.Namespace, sid: str, prompt: str, *extra: str) -> dict:
    cmd = [_cli(), "-p", "--model", args.model, "--output-format", "json", "--resume", sid, *extra, "--tools", ""]
    out = subprocess.run(cmd, cwd=args.cwd, input=prompt, capture_output=True, text=True, encoding="utf-8")
    return json.loads(out.stdout) if out.stdout.strip() else {"error": out.stderr}


async def cli_turn_async(args: argparse.Namespace, sid: str, prompt: str) -> dict:
    return await asyncio.to_thread(cli_turn, args, sid, prompt)


def tree(path: str) -> None:
    print(f"\n--- transcript {os.path.basename(path)}")
    for line in open(path, encoding="utf-8"):
        d = json.loads(line)
        if d.get("type") not in ("user", "assistant"):
            continue
        content = d["message"]["content"]
        text = (
            content
            if isinstance(content, str)
            else " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        )
        print(
            f"{d['type'][:4]} uuid={d['uuid'][:8]} parent={(d.get('parentUuid') or '-')[:8]} "
            f"sid={d['sessionId'][:8]} ep={d.get('entrypoint')} {text[:70]!r}"
        )


async def main_async(args: argparse.Namespace) -> None:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    def opts(**kw: object) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(model=args.model, cwd=args.cwd, tools=[], **kw)

    a = ClaudeSDKClient(options=opts())
    await a.connect()
    text, sid = await ask(a, "Remember the secret word BANANA. Reply only 'ok'.")
    print(f"1. A created {sid}: {text!r}")

    r = cli_turn(args, sid, "Also remember the secret number 42. Reply only 'ok'.")
    print(
        f"2. B resumed: session_id={r.get('session_id')} same={r.get('session_id') == sid} "
        f"result={r.get('result')!r} error={r.get('error', '')[:200]!r}"
    )

    text, sid_a = await ask(a, "What secret word and secret number do you know? One line.")
    print(f"3. A after B: {text!r} (session {sid_a})")

    both = await asyncio.gather(
        ask(a, "Concurrent A: reply only 'A here'."),
        cli_turn_async(args, sid, "Concurrent B: reply only 'B here'."),
    )
    print(f"4. concurrent: A={both[0]!r} B={both[1].get('result')!r} B.session={both[1].get('session_id')}")
    await a.disconnect()

    r = cli_turn(args, sid, "List every secret and every 'here' reply you have seen. One line.")
    print(f"5. fresh resume: {r.get('result')!r} session={r.get('session_id')}")

    f = ClaudeSDKClient(options=opts(resume=sid, fork_session=True))
    await f.connect()
    text, fsid = await ask(f, "Fork check: what secret word do you know? One line.")
    await f.disconnect()
    print(f"6. fork: new id={fsid} differs={fsid != sid} answer={text!r}")

    for s in {sid, fsid}:
        path = find_session_file(s)
        if path:
            tree(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--model", default="claude-haiku-5-5")
    args = ap.parse_args()
    scrub_host_env()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    os.makedirs(args.cwd, exist_ok=True)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
