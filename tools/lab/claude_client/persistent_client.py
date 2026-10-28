"""Persistent client lab: one Claude Code process kept alive across many turns.

Research prototype, not production code. Opens a `ClaudeSDKClient` once (one CLI
child process in streaming-input mode) and sends every line typed on stdin as a new
turn of the same session, printing the reply as it streams plus per-turn latency.

The session is a normal Claude Code transcript on disk, so it can be resumed later
by any client (`claude --resume <id>`, this script with `--resume`).

Usage (venv with claude-agent-sdk):
  python tools/lab/claude_client/persistent_client.py --cwd DIR [--model M]
        [--resume SESSION_ID [--fork]] [--continue] [--session-id UUID]
  Then type prompts; an empty line or Ctrl+Z/Ctrl+D quits. Pipe a file to script it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Variables the Claude Desktop app sets on its own sessions. A child CLI that inherits
# them believes the Desktop host is handing it OAuth tokens; in the lab that child
# rewrote ~/.claude/.credentials.json with empty tokens. Drop them all (by prefix, so new
# ones are covered) so the client behaves like a standalone one, as the VoiceMate
# companion would run. These few are settings the person chose, not host state.
_KEEP_ENV = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_GIT_BASH_PATH"}
_HOST_ENV_NAMES = {"CLAUDECODE", "CLAUDE_PID"}


def scrub_host_env() -> None:
    for name in list(os.environ):
        if name in _KEEP_ENV:
            continue
        if name.startswith("CLAUDE_CODE_") or name in _HOST_ENV_NAMES:
            os.environ.pop(name, None)


async def run(args: argparse.Namespace) -> None:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, ResultMessage, TextBlock
    from claude_agent_sdk.types import StreamEvent

    opts = ClaudeAgentOptions(
        model=args.model,
        cwd=args.cwd,
        tools=[] if args.no_tools else None,
        include_partial_messages=True,
        resume=args.resume,
        continue_conversation=args.continue_,
        fork_session=args.fork,
        session_id=args.session_id,
        setting_sources=["user", "project"] if args.settings else None,
    )
    t0 = time.perf_counter()
    client = ClaudeSDKClient(options=opts)
    await client.connect()
    pid = client._transport._process.pid  # type: ignore[union-attr]  # lab only
    print(f"# connected in {time.perf_counter() - t0:.2f}s, cli pid={pid}", file=sys.stderr, flush=True)

    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            prompt = line.strip()
            if not prompt:
                break
            start = time.perf_counter()
            ttft = None
            streamed = False
            await client.query(prompt)
            async for msg in client.receive_response():
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                        if ttft is None:
                            ttft = time.perf_counter() - start
                        streamed = True
                        print(ev["delta"]["text"], end="", flush=True)
                elif isinstance(msg, AssistantMessage) and not streamed:
                    print("".join(b.text for b in msg.content if isinstance(b, TextBlock)), end="")
                elif isinstance(msg, ResultMessage):
                    total = time.perf_counter() - start
                    cur = client._transport._process.pid  # type: ignore[union-attr]
                    print(
                        f"\n# session={msg.session_id} ttft={ttft if ttft is None else round(ttft, 2)}s "
                        f"total={total:.2f}s pid={cur} error={msg.is_error}",
                        file=sys.stderr,
                        flush=True,
                    )
    finally:
        await client.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True, help="lab directory; --continue picks the latest session of this project")
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--resume")
    ap.add_argument("--continue", dest="continue_", action="store_true")
    ap.add_argument("--fork", action="store_true", help="with --resume: write to a new session id")
    ap.add_argument("--session-id", help="force the id of a new session")
    ap.add_argument("--no-tools", action="store_true", help="answer only, no tools (faster, cheaper)")
    ap.add_argument("--settings", action="store_true", help="load user and project settings/CLAUDE.md")
    args = ap.parse_args()
    scrub_host_env()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
