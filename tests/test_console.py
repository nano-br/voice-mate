"""force_utf8_stdio: UTF-8 without crashing, and every line flushed through a pipe."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import threading
from pathlib import Path

from app.core import console

ROOT = Path(__file__).resolve().parents[1]


def test_reconfigures_to_utf8_and_line_buffering() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict", newline="\n")
    console._reconfigure(stream)
    assert stream.encoding == "utf-8"
    assert stream.errors == "replace"
    assert stream.line_buffering
    stream.write("Transcrevendo → é\n")
    assert raw.getvalue() == "Transcrevendo → é\n".encode()


def test_streams_without_reconfigure_are_ignored() -> None:
    console._reconfigure(None)
    console._reconfigure(io.StringIO())


def test_piped_line_arrives_before_the_process_exits() -> None:
    """Through a pipe a printed line must reach the reader at once, not on exit.

    The child prints, then blocks on stdin until the test kills it: block-buffered, the
    line would never come and the reader times out; there is no clock race to lose.
    """
    script = (
        "import sys\n"
        "from app.core.console import force_utf8_stdio\n"
        "force_utf8_stdio()\n"
        "print('ready')\n"
        "sys.stdin.read()\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
    lines: list[bytes] = []
    with subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        stdout = proc.stdout
        assert stdout is not None
        reader = threading.Thread(target=lambda: lines.append(stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout=60)
        proc.kill()
        _, err = proc.communicate()
    assert lines and lines[0].strip() == b"ready", err.decode(errors="replace")
