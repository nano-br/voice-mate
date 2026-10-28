"""The transcription mode settings -> engine flags, and the `make stt-*` tools run from the app.

The engine picks where it transcribes from `--stt-mode` (cpu: faster-whisper int8 on the
processor, the GPU stays free; gpu: the graphics card) and the Whisper model from
`--model`. The companion keeps one model per mode, so switching modes never asks again.
Installing a mode runs `make stt-install` in the engine checkout (inside WSL on Windows)
and streams its output to the UI. No Qt here: the controller and the tests share it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, cast

from app.companion.contract import CompanionSettings
from app.companion.supervisor.backend import hidden_window_flags
from app.protocol.models import DEFAULT_STT_MODEL, SttCheckReport, SttDevice, SttMode, SttModel

log = logging.getLogger(__name__)

# `make stt-check` imports torch in a subprocess when the GPU mode is AMD on Linux: slow
# on a cold WSL, but never minutes.
CHECK_TIMEOUT_S: Final = 180.0
_STATUS_KEYS: Final = frozenset({"ready", "supported", "detail", "download_mb"})


@dataclass(frozen=True)
class EngineStt:
    """What goes on the engine command line (both values come from closed sets)."""

    mode: SttMode
    model: SttModel

    def cli_args(self) -> str:
        # "auto" leaves the engine's own choice (its saved config, then the best backend).
        return "" if self.mode == "auto" else f"--stt-mode {self.mode} --model {self.model}"


def engine_stt(settings: CompanionSettings) -> EngineStt:
    """The flags for these settings: the model of the chosen mode. "auto" sends no model,
    so neither model counts (changing one must not restart the engine)."""
    if settings.stt_mode == "auto":
        return EngineStt("auto", DEFAULT_STT_MODEL)
    model = settings.stt_cpu_model if settings.stt_mode == "cpu" else settings.stt_gpu_model
    return EngineStt(settings.stt_mode, model)


def check_target(settings: CompanionSettings) -> str:
    return f'stt-check ARGS="--json --cpu-model {settings.stt_cpu_model} --gpu-model {settings.stt_gpu_model}"'


def install_target(settings: CompanionSettings, mode: SttDevice) -> str:
    model = settings.stt_cpu_model if mode == "cpu" else settings.stt_gpu_model
    return f'stt-install MODE={mode} ARGS="--model {model}"'


def parse_check_report(output: str) -> SttCheckReport | None:
    """The JSON line of `make stt-check ARGS=--json` (make and poetry print around it)."""
    for line in reversed(output.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        for mode in ("cpu", "gpu"):
            status = data.get(mode)
            if not isinstance(status, dict) or not _STATUS_KEYS <= status.keys():
                return None
        return cast(SttCheckReport, data)
    return None


def _env() -> dict[str, str]:
    return dict(os.environ, WSL_UTF8="1")  # wsl.exe's own messages in UTF-8


def run_check(command: Sequence[str]) -> SttCheckReport | None:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=CHECK_TIMEOUT_S,
            env=_env(),
            creationflags=hidden_window_flags(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("stt-check failed to run: %s", exc)
        return None
    output = result.stdout.decode("utf-8", errors="replace")
    report = parse_check_report(output)
    if report is None:
        log.warning("stt-check gave no report (exit %s): %s", result.returncode, output[-500:])
    return report


def run_install(command: Sequence[str], on_line: Callable[[str], None]) -> bool:
    """Run the install, handing each output line to `on_line`; True on exit code 0."""
    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=_env(),
            creationflags=hidden_window_flags(),
        )
    except OSError as exc:
        log.warning("stt-install failed to start: %s", exc)
        on_line(str(exc))
        return False
    assert process.stdout is not None
    with process.stdout:
        for raw in process.stdout:
            # pip draws progress bars with carriage returns: keep the last state of the line.
            line = raw.decode("utf-8", errors="replace").rstrip().rsplit("\r", 1)[-1]
            if line:
                on_line(line)
    code = process.wait()
    log.info("stt-install finished with exit code %s", code)
    return code == 0
