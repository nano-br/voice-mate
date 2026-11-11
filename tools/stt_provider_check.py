"""Conformance check for an external STT provider (`make stt-provider-check URL=...`).

Runs the provider through docs/stt-provider-protocol.md the way VoiceMate does:

1. `/v1/info`: protocol name and version, the sample rates, `pcm_s16le`, the languages.
2. `/v1/health`: answers `ok` (waits while it says `loading`).
3. `/v1/transcribe` with a short generated tone: answers `{"text": ...}`. Reports the latency.
4. `/v1/transcribe` with silence: an empty text (a warning when the provider hears something).
5. `/v1/transcribe` with your own recording (`--wav`): prints what the provider heard.
6. A sample rate the provider did not list is refused with 400 `unsupported_format` (a warning
   when it is refused with another code, a failure when it is accepted as if nothing was wrong).

Exit code 0 when nothing failed (warnings do not fail), 1 otherwise. Standard library only;
the protocol client is the one VoiceMate itself uses (app/protocol/stt_provider.py).
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import sys
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from app.protocol.stt_provider import (
    DEFAULT_URL,
    TOKEN_ENV,
    ProviderError,
    SttProviderClient,
    SttProviderInfo,
    Transcription,
)

RECORDING_RATE: Final = 16000  # what VoiceMate records at
TONE_SECONDS: Final = 1.5
SILENCE_SECONDS: Final = 1.0
HEALTH_WAIT_S: Final = 60.0
LATENCY_WARN_S: Final = 2.0
UNLISTED_RATE: Final = 7351  # no provider lists this one


class Report:
    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def ok(self, message: str) -> None:
        print(f"  ok    {message}")

    def warn(self, message: str) -> None:
        self.warnings += 1
        print(f"  warn  {message}")

    def fail(self, message: str) -> None:
        self.failures += 1
        print(f"  FAIL  {message}")

    def step(self, title: str) -> None:
        print(f"\n{title}")


# --- audio (16-bit mono PCM, little endian) ---------------------------------------------------------------


def _pcm(samples: Sequence[float]) -> bytes:
    data = array.array("h", (max(-32768, min(32767, round(value * 32767))) for value in samples))
    if sys.byteorder == "big":
        data.byteswap()
    return data.tobytes()


def tone(rate: int, seconds: float = TONE_SECONDS, hz: float = 440.0) -> bytes:
    count = int(rate * seconds)
    return _pcm([0.3 * math.sin(2 * math.pi * hz * n / rate) for n in range(count)])


def silence(rate: int, seconds: float = SILENCE_SECONDS) -> bytes:
    return bytes(2 * int(rate * seconds))


def read_wav(path: Path, rate: int) -> bytes:
    """A 16-bit WAV as mono PCM at `rate` (channels averaged, linear resampling)."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError(f"{path} is not 16-bit PCM")
        channels, source_rate = wav.getnchannels(), wav.getframerate()
        frames = array.array("h", wav.readframes(wav.getnframes()))
    if sys.byteorder == "big":
        frames.byteswap()
    mono = [sum(frames[i : i + channels]) / channels / 32768.0 for i in range(0, len(frames), channels)]
    if source_rate != rate and mono:
        count = max(1, round(len(mono) * rate / source_rate))
        step = source_rate / rate
        resampled = []
        for n in range(count):
            position = n * step
            left = min(int(position), len(mono) - 1)
            right = min(left + 1, len(mono) - 1)
            resampled.append(mono[left] + (mono[right] - mono[left]) * (position - left))
        mono = resampled
    return _pcm(mono)


# --- steps ------------------------------------------------------------------------------------------------


def check_info(client: SttProviderClient, report: Report) -> SttProviderInfo | None:
    report.step("1. GET /v1/info")
    try:
        info = client.info()
    except ProviderError as exc:
        report.fail(exc.detail)
        return None
    report.ok(f"protocol voicemate-stt v1, name '{info.name}'")
    report.ok(f"sample rates {', '.join(map(str, info.sample_rates))} Hz, formats {', '.join(info.formats)}")
    report.ok(f"languages: {', '.join(info.languages) or 'any'}")
    if RECORDING_RATE not in info.sample_rates:
        report.warn(f"{RECORDING_RATE} Hz is not listed: VoiceMate records at it and resamples for this provider")
    if info.max_seconds is None:
        report.warn("max_seconds is missing: VoiceMate will send recordings of any length")
    else:
        report.ok(f"max_seconds {info.max_seconds:g}")
    report.ok(f"features: {', '.join(info.features) or 'none'}")
    return info


def check_health(client: SttProviderClient, report: Report) -> bool:
    report.step("2. GET /v1/health")
    deadline = time.monotonic() + HEALTH_WAIT_S
    announced = False
    while True:
        try:
            status = client.health()
        except ProviderError as exc:
            report.fail(exc.detail)
            return False
        if status == "ok":
            report.ok("status ok")
            return True
        if time.monotonic() > deadline:
            report.fail(f"still 'loading' after {HEALTH_WAIT_S:.0f} s")
            return False
        if not announced:
            print("  ...   loading, waiting")
            announced = True
        time.sleep(1.0)


def _transcribe(
    client: SttProviderClient,
    pcm: bytes,
    rate: int,
    language: str | None,
    options: dict[str, Any],
    report: Report,
) -> Transcription | None:
    started = time.monotonic()
    try:
        result = client.transcribe(pcm, sample_rate=rate, language=language, options=options)
    except ProviderError as exc:
        report.fail(exc.detail)
        return None
    elapsed = time.monotonic() - started
    seconds = len(pcm) / 2 / rate
    report.ok(f"{seconds:.1f} s of audio transcribed in {elapsed * 1000:.0f} ms")
    if elapsed > LATENCY_WARN_S and elapsed > seconds:
        report.warn(f"it took {elapsed:.1f} s, longer than the audio itself: dictation will feel slow")
    if result.language:
        report.ok(f"language heard: {result.language}")
    return result


def check_tone(
    client: SttProviderClient, info: SttProviderInfo, language: str | None, options: dict[str, Any], report: Report
) -> bool:
    report.step("3. POST /v1/transcribe (a short tone)")
    rate = info.sample_rate_for(RECORDING_RATE)
    result = _transcribe(client, tone(rate), rate, language, options, report)
    if result is None:
        return False
    report.ok(f"text: {result.text!r}" if result.text else "text: empty (nothing heard)")
    return True


def check_silence(
    client: SttProviderClient, info: SttProviderInfo, language: str | None, options: dict[str, Any], report: Report
) -> None:
    report.step("4. POST /v1/transcribe (silence)")
    rate = info.sample_rate_for(RECORDING_RATE)
    result = _transcribe(client, silence(rate), rate, language, options, report)
    if result is None:
        return
    if result.text:
        report.warn(f"silence was heard as {result.text!r}: VoiceMate would paste that text")
    else:
        report.ok("empty text: nothing heard")


def check_wav(
    client: SttProviderClient,
    info: SttProviderInfo,
    path: Path,
    language: str | None,
    options: dict[str, Any],
    report: Report,
) -> None:
    report.step(f"5. POST /v1/transcribe ({path.name})")
    rate = info.sample_rate_for(RECORDING_RATE)
    try:
        pcm = read_wav(path, rate)
    except (OSError, ValueError, wave.Error) as exc:
        report.fail(f"cannot read {path}: {exc}")
        return
    if info.max_seconds is not None and len(pcm) / 2 / rate > info.max_seconds:
        report.warn(f"the recording is longer than max_seconds ({info.max_seconds:g} s): VoiceMate would not send it")
    result = _transcribe(client, pcm, rate, language, options, report)
    if result is not None:
        report.ok(f"text: {result.text!r}" if result.text else "text: empty (nothing heard)")


def check_unlisted_rate(client: SttProviderClient, info: SttProviderInfo, report: Report) -> None:
    report.step("6. A sample rate the provider did not list")
    if UNLISTED_RATE in info.sample_rates:
        report.ok(f"skipped: the provider lists {UNLISTED_RATE} Hz")
        return
    try:
        result = client.transcribe(silence(UNLISTED_RATE, 0.2), sample_rate=UNLISTED_RATE)
    except ProviderError as exc:
        if exc.code == "unsupported_format":
            report.ok("refused with 400 unsupported_format")
        elif exc.kind == "rejected":
            report.warn(f"refused, but with {exc.detail} instead of 400 unsupported_format")
        else:
            report.fail(f"expected 400 unsupported_format, got {exc.detail}")
        return
    report.fail(f"audio at {UNLISTED_RATE} Hz was accepted (text {result.text!r}) although /v1/info does not list it")


def run(
    url: str,
    token: str | None,
    wav: Path | None = None,
    language: str | None = None,
    options: dict[str, Any] | None = None,
) -> int:
    print(f"Checking the STT provider at {url}")
    try:
        client = SttProviderClient(url, token)
    except ValueError as exc:
        print(f"  FAIL  {exc}")
        return 1
    options = options or {}
    report = Report()
    info = check_info(client, report)
    if info is None:
        print("\nResult: the provider is not reachable or not compatible, so the rest was skipped.")
        return 1
    if language is not None and not info.accepts_language(language):
        report.warn(f"the provider does not list '{language}': VoiceMate would not send it recordings in it")
    if check_health(client, report) and check_tone(client, info, language, options, report):
        check_silence(client, info, language, options, report)
        if wav is not None:
            check_wav(client, info, wav, language, options, report)
        check_unlisted_rate(client, info, report)
    verdict = "FAILED" if report.failures else "PASSED"
    print(f"\nResult: {verdict} ({report.failures} failures, {report.warnings} warnings)")
    return 1 if report.failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an external STT provider against protocol v1.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"provider base URL (default {DEFAULT_URL})")
    parser.add_argument("--token", default=None, help=f"bearer token (default: the {TOKEN_ENV} environment variable)")
    parser.add_argument("--wav", default=None, help="a 16-bit WAV recording to transcribe as well")
    parser.add_argument("--language", default=None, help="ISO 639-1 code sent with each request (default: none)")
    parser.add_argument("--options", default=None, help="JSON object sent as X-VoiceMate-Options")
    args = parser.parse_args(argv)
    options: dict[str, Any] = {}
    if args.options:
        try:
            parsed = json.loads(args.options)
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            parser.error("--options must be a JSON object")
        options = parsed
    token = args.token or os.environ.get(TOKEN_ENV) or None
    return run(args.url, token, Path(args.wav) if args.wav else None, args.language, options)


if __name__ == "__main__":
    sys.exit(main())
