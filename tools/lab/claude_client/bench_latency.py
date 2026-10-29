"""Latency lab: persistent ClaudeSDKClient vs one process per message.

Research prototype, not production code. Measures, for each strategy, the time
to the first text token (TTFT) and to the end of the turn, plus the PID of the
CLI child process so we can see whether it survives between turns.

Strategies:
  sdk-persistent  one ClaudeSDKClient kept open, N turns on the same process
  sdk-query       claude_agent_sdk.query() per message (new process each time)
  cli-resume      `claude -p --resume <id>` per message (new process each time)
  cli-stream      one `claude -p --input-format stream-json` process, N turns on stdin

Usage (from a venv with claude-agent-sdk installed):
  python tools/lab/claude_client/bench_latency.py --turns 4 --cwd <lab dir> [--rounds 3]

--cwd is required and must be a lab directory: every turn writes a session there.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions

PROMPTS = [
    "Reply with exactly one short sentence: what is 2 + 2?",
    "Reply with exactly one short sentence: what number did I ask about before?",
    "Reply with exactly one short sentence: name a colour.",
    "Reply with exactly one short sentence: what is 10 times 3?",
    "Reply with exactly one short sentence: say goodbye.",
]


@dataclass
class Turn:
    strategy: str
    turn: int
    ttft_s: float | None
    total_s: float
    pid: int | None
    session_id: str | None
    text: str
    round: int = 0


def _now() -> float:
    return time.perf_counter()


def _options(args: argparse.Namespace, **extra: object) -> ClaudeAgentOptions:
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        model=args.model,
        cwd=args.cwd,
        tools=[],
        include_partial_messages=True,
        max_turns=1,
        **extra,  # type: ignore[arg-type]
    )


def _delta_text(msg: object) -> str | None:
    """Text from a partial `content_block_delta` stream event, if any."""
    from claude_agent_sdk.types import StreamEvent

    if isinstance(msg, StreamEvent):
        ev = msg.event
        if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            return str(ev["delta"].get("text", ""))
    return None


async def bench_sdk_persistent(args: argparse.Namespace) -> list[Turn]:
    from claude_agent_sdk import ClaudeSDKClient, ResultMessage

    out: list[Turn] = []
    t0 = _now()
    client = ClaudeSDKClient(options=_options(args))
    await client.connect()
    connect_s = _now() - t0
    pid = client._transport._process.pid  # type: ignore[union-attr]  # lab only: private attribute
    print(f"[sdk-persistent] connect() took {connect_s:.2f}s, pid={pid}")
    try:
        for i in range(args.turns):
            start = _now()
            ttft = None
            text: list[str] = []
            sid = None
            await client.query(PROMPTS[i % len(PROMPTS)])
            async for msg in client.receive_response():
                d = _delta_text(msg)
                if d is not None:
                    if ttft is None:
                        ttft = _now() - start
                    text.append(d)
                if isinstance(msg, ResultMessage):
                    sid = msg.session_id
            cur_pid = client._transport._process.pid  # type: ignore[union-attr]
            out.append(Turn("sdk-persistent", i, ttft, _now() - start, cur_pid, sid, "".join(text).strip()))
    finally:
        await client.disconnect()
    out[0].total_s += connect_s  # the first turn pays the spawn, report it honestly
    if out[0].ttft_s is not None:
        out[0].ttft_s += connect_s
    return out


async def bench_sdk_query(args: argparse.Namespace) -> list[Turn]:
    from claude_agent_sdk import ResultMessage, query

    out: list[Turn] = []
    sid: str | None = None
    for i in range(args.turns):
        start = _now()
        ttft = None
        text: list[str] = []
        opts = _options(args, resume=sid) if sid else _options(args)
        async for msg in query(prompt=PROMPTS[i % len(PROMPTS)], options=opts):
            d = _delta_text(msg)
            if d is not None:
                if ttft is None:
                    ttft = _now() - start
                text.append(d)
            if isinstance(msg, ResultMessage):
                sid = msg.session_id
        out.append(Turn("sdk-query", i, ttft, _now() - start, None, sid, "".join(text).strip()))
    return out


def _cli() -> str:
    # Same binary as the SDK by default, so every strategy runs the same CLI version.
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    bundled = SubprocessCLITransport._find_bundled_cli(None)  # type: ignore[arg-type]  # lab only
    return os.environ.get("LAB_CLI") or bundled or shutil.which("claude") or "claude"


def _read_stream_turn(proc: subprocess.Popen[str], start: float) -> tuple[float | None, str, str | None]:
    ttft = None
    text: list[str] = []
    sid = None
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        if ev.get("type") == "stream_event":
            e = ev["event"]
            if e.get("type") == "content_block_delta" and e["delta"].get("type") == "text_delta":
                if ttft is None:
                    ttft = _now() - start
                text.append(e["delta"]["text"])
        elif ev.get("type") == "result":
            sid = ev.get("session_id")
            break
    return ttft, "".join(text).strip(), sid


def _cli_base(args: argparse.Namespace) -> list[str]:
    return [
        _cli(),
        "-p",
        "--model",
        args.model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools",
        "",
    ]


def bench_cli_resume(args: argparse.Namespace) -> list[Turn]:
    out: list[Turn] = []
    sid: str | None = None
    for i in range(args.turns):
        # The prompt goes on stdin: `--tools` is variadic and would swallow a trailing argument.
        cmd = _cli_base(args) + (["--resume", sid] if sid else [])
        start = _now()
        proc = subprocess.Popen(
            cmd, cwd=args.cwd, stdout=subprocess.PIPE, stdin=subprocess.PIPE, text=True, encoding="utf-8"
        )
        assert proc.stdin is not None
        proc.stdin.write(PROMPTS[i % len(PROMPTS)])
        proc.stdin.close()
        ttft, text, sid = _read_stream_turn(proc, start)
        proc.wait()
        out.append(Turn("cli-resume", i, ttft, _now() - start, proc.pid, sid, text))
    return out


def bench_cli_stream(args: argparse.Namespace) -> list[Turn]:
    out: list[Turn] = []
    cmd = _cli_base(args) + ["--input-format", "stream-json"]
    spawn = _now()
    proc = subprocess.Popen(
        cmd, cwd=args.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )
    assert proc.stdin is not None
    try:
        for i in range(args.turns):
            start = spawn if i == 0 else _now()
            msg = {"type": "user", "message": {"role": "user", "content": PROMPTS[i % len(PROMPTS)]}}
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            ttft, text, sid = _read_stream_turn(proc, start)
            out.append(Turn("cli-stream", i, ttft, _now() - start, proc.pid, sid, text))
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
    return out


def _summary(turns: list[Turn]) -> str:
    first, rest = turns[0], turns[1:]

    def med(xs: list[float]) -> str:
        return f"{statistics.median(xs):.2f}s" if xs else "n/a"

    pids = {t.pid for t in turns if t.pid}
    return (
        f"{first.strategy:15s} turn1 ttft={first.ttft_s or 0:.2f}s total={first.total_s:.2f}s | "
        f"next median ttft={med([t.ttft_s for t in rest if t.ttft_s])} "
        f"total={med([t.total_s for t in rest])} | distinct pids={len(pids) or 'n/a'}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=4)
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--cwd", required=True, help="lab directory, not a real project: every turn writes a session")
    ap.add_argument("--only", nargs="*", default=["sdk-persistent", "sdk-query", "cli-resume", "cli-stream"])
    ap.add_argument("--rounds", type=int, default=1, help="repeat all strategies; the order rotates each round")
    ap.add_argument("--json", help="write raw turns to this file")
    args = ap.parse_args()
    from persistent_client import scrub_host_env

    scrub_host_env()  # never let the lab CLI think the Desktop host owns its auth
    os.makedirs(args.cwd, exist_ok=True)

    results: list[Turn] = []
    runners = {
        "sdk-persistent": lambda: asyncio.run(bench_sdk_persistent(args)),
        "sdk-query": lambda: asyncio.run(bench_sdk_query(args)),
        "cli-resume": lambda: bench_cli_resume(args),
        "cli-stream": lambda: bench_cli_stream(args),
    }
    for rnd in range(args.rounds):
        # Rotate the order so no strategy always pays the cold prompt cache of the first run.
        k = rnd % len(args.only)
        for name in args.only[k:] + args.only[:k]:
            turns = runners[name]()
            for t in turns:
                t.round = rnd
                print(
                    f"  r{rnd} {t.strategy} #{t.turn} ttft={t.ttft_s} total={t.total_s:.2f} pid={t.pid} "
                    f"sid={t.session_id} text={t.text[:60]!r}"
                )
            print(_summary(turns))
            results.extend(turns)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in results], f, indent=2)


if __name__ == "__main__":
    main()
