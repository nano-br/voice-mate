"""Transcription mode settings -> engine flags, and the `make stt-*` report parsing."""

from __future__ import annotations

import json
import sys

import pytest

from app.companion import stt
from app.companion.contract import CompanionSettings
from app.companion.stt import (
    EngineStt,
    check_target,
    engine_stt,
    install_target,
    parse_check_report,
    run_check,
    run_install,
)
from app.companion.supervisor.backend import engine_script

_READY = {"ready": True, "supported": True, "detail": "", "download_mb": 0}


def test_auto_leaves_the_engine_its_own_choice() -> None:
    assert engine_stt(CompanionSettings()).cli_args() == ""


def test_each_mode_brings_its_own_model() -> None:
    settings = CompanionSettings(stt_cpu_model="medium", stt_gpu_model="large-v3")
    assert engine_stt(settings) == engine_stt(CompanionSettings())  # auto sends no model: neither counts
    assert engine_stt(CompanionSettings(stt_mode="cpu", stt_cpu_model="medium")).cli_args() == (
        "--stt-mode cpu --model medium"
    )
    assert engine_stt(CompanionSettings(stt_mode="gpu", stt_gpu_model="small")).cli_args() == (
        "--stt-mode gpu --model small"
    )


def test_engine_script_adds_the_stt_flags() -> None:
    script = engine_script("ai-lab/voice-mate", 47821, stt=EngineStt("cpu", "medium"))
    assert (
        script
        == 'cd "$HOME/ai-lab/voice-mate" && exec make run-engine ARGS="--daemon-port 47821 --stt-mode cpu --model medium"'
    )
    assert "--stt-mode" not in engine_script("ai-lab/voice-mate", 47821, stt=EngineStt("auto", "medium"))


def test_targets_name_the_models_of_each_mode() -> None:
    settings = CompanionSettings(stt_cpu_model="medium", stt_gpu_model="small")
    assert check_target(settings) == 'stt-check ARGS="--json --cpu-model medium --gpu-model small"'
    assert install_target(settings, "cpu") == 'stt-install MODE=cpu ARGS="--model medium"'
    assert install_target(settings, "gpu") == 'stt-install MODE=gpu ARGS="--model small"'


def test_the_report_is_the_last_json_line() -> None:
    report = {"cpu": _READY, "gpu": dict(_READY, ready=False, download_mb=4300)}
    output = f"poetry run ...\n{json.dumps(report)}\nmake: leaving directory\n"
    assert parse_check_report(output) == report


def test_broken_or_incomplete_reports_are_none() -> None:
    assert parse_check_report("") is None
    assert parse_check_report("{not json") is None
    assert parse_check_report("[1, 2]") is None
    assert parse_check_report(json.dumps({"cpu": _READY})) is None
    assert parse_check_report(json.dumps({"cpu": _READY, "gpu": {"ready": True}})) is None


def test_install_streams_lines_and_keeps_the_last_progress_state() -> None:
    code = r"import sys; sys.stdout.write('one\n10%\r50%\r100%\n\ntwo\n'); sys.exit(0)"
    lines: list[str] = []
    assert run_install([sys.executable, "-c", code], lines.append)
    assert lines == ["one", "100%", "two"]


def test_install_failure_is_false() -> None:
    lines: list[str] = []
    assert not run_install([sys.executable, "-c", "raise SystemExit(3)"], lines.append)
    assert not run_install(["voicemate-no-such-program"], lines.append)
    assert lines  # the reason it could not start


def test_check_reads_the_report_through_make_noise() -> None:
    report = json.dumps({"cpu": _READY, "gpu": _READY})
    code = f"print('make: entering'); print({report!r}); print('make: leaving')"
    assert run_check([sys.executable, "-c", code]) == {"cpu": _READY, "gpu": _READY}


def test_check_that_hangs_or_cannot_start_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stt, "CHECK_TIMEOUT_S", 0.5)
    assert run_check([sys.executable, "-c", "import time; time.sleep(5)"]) is None
    assert run_check(["voicemate-no-such-program"]) is None
