"""How many capture devices the audio server (PipeWire/PulseAudio) has, via `pactl`."""

from __future__ import annotations

import shutil
import subprocess


def parse_sources(output: str) -> int:
    """`pactl list short sources` lines: id, name, driver, format, state. Monitors are not mics."""
    count = 0
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] and not fields[1].endswith(".monitor"):
            count += 1
    return count


def active_capture_count() -> int:
    """Number of capture sources, or -1 when it cannot be determined."""
    pactl = shutil.which("pactl")
    if pactl is None:
        return -1
    try:
        result = subprocess.run(
            [pactl, "list", "short", "sources"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return -1
    if result.returncode != 0:
        return -1
    return parse_sources(result.stdout)
