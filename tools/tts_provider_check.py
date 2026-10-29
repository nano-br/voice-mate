"""Conformance check for an external TTS provider (`make tts-provider-check URL=...`).

Runs the provider through docs/tts-provider-protocol.md the way VoiceMate does:

1. `/v1/info`: protocol name and version, a sample rate, `pcm_s16le`, the voices.
2. `/v1/health`: answers `ok` (waits while it says `loading`).
3. `/v1/speak`: streams mono PCM at the advertised rate. Reports the time to the first byte
   and the total time, and writes the audio to a .wav file you can listen to.
4. An unknown voice is refused with 400 `unknown_voice` (a warning when it is not).
5. `/v1/cancel`: stops a long request within about 200 ms, and the next request is not
   refused as busy.
6. Closing the connection without `/v1/cancel` stops the synthesis too: the next request is
   not refused as busy (VoiceMate relies on this when the person interrupts and the cancel
   call does not get through).

Exit code 0 when nothing failed (warnings do not fail), 1 otherwise. Standard library only;
the protocol client is the one VoiceMate itself uses (app/protocol/tts_provider.py).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from app.protocol.tts_provider import (
    DEFAULT_URL,
    FEATURE_STREAMING,
    FORMAT_S16,
    TOKEN_ENV,
    ProviderClient,
    ProviderError,
    ProviderInfo,
    SpeechStream,
)

SENTENCE: Final = "Hello, this is a short test of the VoiceMate text to speech provider protocol."
HEALTH_WAIT_S: Final = 60.0
CANCEL_PASS_S: Final = 0.3  # "about 200 ms" in the protocol, with room for HTTP
CANCEL_WARN_S: Final = 1.0
HANGUP_PASS_S: Final = 0.5  # how soon after a hang-up the provider takes the next request
HANGUP_FAIL_S: Final = 3.0
FIRST_BYTE_WARN_S: Final = 2.0


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


def check_info(client: ProviderClient, report: Report) -> ProviderInfo | None:
    report.step("1. GET /v1/info")
    try:
        info = client.info()
    except ProviderError as exc:
        report.fail(exc.detail)
        return None
    report.ok(f"protocol voicemate-tts v1, name '{info.name}'")
    report.ok(f"{info.sample_rate} Hz, formats {', '.join(info.formats)}")
    voices = ", ".join(voice.id for voice in info.voices) or "none listed"
    report.ok(f"{len(info.voices)} voices: {voices}")
    if info.voices and not any(voice.default for voice in info.voices):
        report.warn("no voice is marked as default")
    if info.max_chars is None:
        report.warn("max_chars is missing: VoiceMate will send whole sentences without splitting them")
    if FEATURE_STREAMING not in info.features:
        report.warn("the 'streaming' feature is not advertised: speech starts only after the whole text is made")
    report.ok(f"features: {', '.join(info.features) or 'none'}")
    return info


def check_health(client: ProviderClient, report: Report) -> bool:
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


def check_speak(
    client: ProviderClient, info: ProviderInfo, voice: str | None, text: str, out: Path, report: Report
) -> bool:
    report.step("3. POST /v1/speak")
    started = time.monotonic()
    try:
        stream = client.speak(text, voice=voice, audio_format=FORMAT_S16, default_rate=info.sample_rate)
    except ProviderError as exc:
        report.fail(exc.detail)
        return False
    audio = bytearray()
    first_byte: float | None = None
    try:
        for chunk in stream.chunks():
            if first_byte is None:
                first_byte = time.monotonic() - started
            audio += chunk
    except ProviderError as exc:
        report.fail(exc.detail)
        return False
    finally:
        stream.close()
    total = time.monotonic() - started
    good = True
    if stream.audio_format != FORMAT_S16:
        report.fail(f"asked for {FORMAT_S16}, got {stream.audio_format}")
        good = False
    if stream.channels != 1:
        report.fail(f"the audio must be mono, got {stream.channels} channels")
        good = False
    if stream.sample_rate != info.sample_rate:
        report.warn(f"X-VoiceMate-Sample-Rate {stream.sample_rate} differs from /v1/info {info.sample_rate}")
    if not audio:
        report.fail("the response had no audio")
        return False
    if len(audio) % 2:
        report.fail("the PCM has an odd number of bytes (pcm_s16le is two bytes per sample)")
        good = False
    seconds = len(audio) / 2 / stream.sample_rate
    assert first_byte is not None
    report.ok(f"time to first byte {first_byte * 1000:.0f} ms, finished in {total:.2f} s")
    report.ok(f"{len(audio)} bytes = {seconds:.2f} s of audio at {stream.sample_rate} Hz")
    if first_byte > FIRST_BYTE_WARN_S:
        report.warn(f"the first byte took {first_byte:.1f} s: speech will feel slow to start")
    if first_byte > 0.8 * total and total > 1.0:
        report.warn("the audio arrived all at once at the end: it is not streaming")
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(stream.sample_rate)
        wav.writeframes(bytes(audio))
    report.ok(f"saved {out}")
    return good


def check_unknown_voice(client: ProviderClient, info: ProviderInfo, report: Report) -> None:
    report.step("4. Unknown voice")
    try:
        stream = client.speak("Hello.", voice="no-such-voice-id", default_rate=info.sample_rate)
    except ProviderError as exc:
        if exc.code == "unknown_voice":
            report.ok("refused with 400 unknown_voice")
        else:
            report.warn(f"refused, but with {exc.detail} instead of 400 unknown_voice")
        return
    stream.close()
    report.warn("an unknown voice was accepted: VoiceMate cannot tell the person the voice id is wrong")


def _long_text(info: ProviderInfo) -> str:
    limit = min(info.max_chars or 600, 600)
    text = SENTENCE
    while len(text) + len(SENTENCE) + 1 <= limit:
        text += " " + SENTENCE
    return text


def check_cancel(client: ProviderClient, info: ProviderInfo, voice: str | None, report: Report) -> bool:
    report.step("5. POST /v1/cancel")
    try:
        stream = client.speak(_long_text(info), voice=voice, default_rate=info.sample_rate)
    except ProviderError as exc:
        report.fail(exc.detail)
        return False
    ended = threading.Event()
    state: dict[str, float | int] = {"after": -1.0, "bytes": 0}

    def drain(stream: SpeechStream) -> None:
        try:
            for chunk in stream.chunks():
                state["bytes"] += len(chunk)
        except ProviderError:
            pass
        finally:
            ended.set()

    reader = threading.Thread(target=drain, args=(stream,), daemon=True)
    reader.start()
    deadline = time.monotonic() + 10.0
    while state["bytes"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    if state["bytes"] == 0:
        stream.close()
        report.fail("no audio arrived within 10 s, so there was nothing to cancel")
        return False
    time.sleep(0.1)
    started = time.monotonic()
    try:
        client.cancel(stream.request_id)
    except ProviderError as exc:
        stream.close()
        report.fail(f"/v1/cancel: {exc.detail}")
        return False
    stopped = ended.wait(5.0)
    elapsed = time.monotonic() - started
    stream.close()
    if not stopped:
        report.fail("the audio kept coming 5 s after /v1/cancel")
        return False
    if elapsed <= CANCEL_PASS_S:
        report.ok(f"the audio stopped {elapsed * 1000:.0f} ms after /v1/cancel")
    elif elapsed <= CANCEL_WARN_S:
        report.warn(f"the audio stopped {elapsed * 1000:.0f} ms after /v1/cancel (the protocol asks for about 200 ms)")
    else:
        report.fail(f"the audio stopped {elapsed:.1f} s after /v1/cancel (the protocol asks for about 200 ms)")
        return False
    # The next sentence must not be refused as busy because the cancelled request lingers.
    try:
        again = client.speak("Hello.", voice=voice, default_rate=info.sample_rate)
    except ProviderError as exc:
        report.fail(f"a request right after a cancel failed: {exc.detail}")
        return False
    again.close()
    report.ok("a new request right after the cancel is accepted")
    return True


def check_hangup(client: ProviderClient, info: ProviderInfo, voice: str | None, report: Report) -> bool:
    report.step("6. Closing the connection without /v1/cancel")
    try:
        # The previous step's last request may still be winding down: not what is under test here.
        for attempt in range(40):
            try:
                stream = client.speak(_long_text(info), voice=voice, default_rate=info.sample_rate)
                break
            except ProviderError as exc:
                if exc.kind != "busy" or attempt == 39:
                    raise
                time.sleep(0.05)
        first = next(stream.chunks(), b"")
    except ProviderError as exc:
        report.fail(exc.detail)
        return False
    if not first:
        stream.close()
        report.fail("no audio arrived, so there was nothing to hang up on")
        return False
    stream.close()  # only the connection: no /v1/cancel
    started = time.monotonic()
    while True:
        try:
            again = client.speak("Hello.", voice=voice, default_rate=info.sample_rate)
        except ProviderError as exc:
            waited = time.monotonic() - started
            if exc.kind != "busy" or waited > HANGUP_FAIL_S:
                if exc.kind == "busy":
                    report.fail(
                        f"still busy {waited:.1f} s after the connection was closed: it must stop the synthesis"
                    )
                else:
                    report.fail(f"a request after the hang-up failed: {exc.detail}")
                return False
            time.sleep(0.05)
            continue
        again.close()
        waited = time.monotonic() - started
        break
    if waited <= HANGUP_PASS_S:
        report.ok("a request right after the hang-up is accepted")
    else:
        report.warn(
            f"the provider was busy for {waited * 1000:.0f} ms after the hang-up (the protocol asks for an immediate stop)"
        )
    return True


def run(url: str, token: str | None, voice: str | None, text: str, out: Path) -> int:
    print(f"Checking the TTS provider at {url}")
    try:
        client = ProviderClient(url, token)
    except ValueError as exc:
        print(f"  FAIL  {exc}")
        return 1
    report = Report()
    info = check_info(client, report)
    if info is None:
        print("\nResult: the provider is not reachable or not compatible, so the rest was skipped.")
        return 1
    if check_health(client, report):
        if check_speak(client, info, voice, text, out, report):
            check_unknown_voice(client, info, report)
            check_cancel(client, info, voice, report)
            check_hangup(client, info, voice, report)
    verdict = "FAILED" if report.failures else "PASSED"
    print(f"\nResult: {verdict} ({report.failures} failures, {report.warnings} warnings)")
    return 1 if report.failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check an external TTS provider against protocol v1.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"provider base URL (default {DEFAULT_URL})")
    parser.add_argument("--token", default=None, help=f"bearer token (default: the {TOKEN_ENV} environment variable)")
    parser.add_argument("--voice", default=None, help="voice id to speak with (default: the provider's default)")
    parser.add_argument("--text", default=SENTENCE, help="the sentence to speak")
    parser.add_argument("--out", default=None, help="where to write the .wav (default: a temporary file)")
    args = parser.parse_args(argv)
    if args.out:
        out = Path(args.out)
    else:
        handle, name = tempfile.mkstemp(prefix="voicemate-tts-check-", suffix=".wav")
        os.close(handle)
        out = Path(name)
    token = args.token or os.environ.get(TOKEN_ENV) or None
    return run(args.url, token, args.voice, args.text, out)


if __name__ == "__main__":
    sys.exit(main())
