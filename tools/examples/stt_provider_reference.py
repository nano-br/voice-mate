"""A reference STT provider for VoiceMate's external provider protocol, version 1.

It implements every endpoint of docs/stt-provider-protocol.md with the standard library only and
"transcribes" each recording into a deterministic sentence that says how long it was, so the
protocol, VoiceMate's client and `make stt-provider-check` can be tried without any real
speech-to-text. A recording of pure silence is heard as nothing (an empty text). Read it as the
smallest working example when writing your own provider.

    python -m tools.examples.stt_provider_reference                   # http://127.0.0.1:8766
    python -m tools.examples.stt_provider_reference --port 9000 --token secret
    python -m tools.examples.stt_provider_reference --loading-seconds 5 --languages pt en

Then, in another terminal: make stt-provider-check URL=http://127.0.0.1:8766
"""

from __future__ import annotations

import argparse
import array
import json
import re
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

SAMPLE_RATES: Final = (16000,)
MAX_SECONDS: Final = 600
# Below this peak (in 16-bit units, about -50 dBFS) the recording is silence: nothing was heard.
SILENCE_PEAK: Final = 100
_LANGUAGE: Final = re.compile(r"[a-z]{2}")


@dataclass
class ProviderState:
    """What the server knows; tests read `requests`."""

    token: str | None = None
    languages: tuple[str, ...] = ()  # empty = any language
    loading_until: float = 0.0  # monotonic time; /v1/health says "loading" before it
    lock: threading.Lock = field(default_factory=threading.Lock)
    busy: bool = False
    requests: list[dict[str, Any]] = field(default_factory=list)  # headers and size of every transcription
    warmups: int = 0


def describe(pcm: bytes, sample_rate: int) -> str:
    """The deterministic "transcription": the duration of the audio, or "" for silence."""
    count = len(pcm) // 2
    if count == 0:
        return ""
    samples = array.array("h", pcm[: count * 2])
    if sys.byteorder == "big":
        samples.byteswap()  # pcm_s16le is little endian
    if max(max(samples), -min(samples)) < SILENCE_PEAK:
        return ""
    return f"I heard {count / sample_rate:.1f} seconds of audio."


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ProviderServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        if self.server.verbose:
            super().log_message(format, *args)

    # --- plumbing ----------------------------------------------------------------------------------------

    def _send_json(self, status: int, body: dict[str, Any], *, close: bool = False) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if close:
            self.send_header("Connection", "close")  # also ends the keep-alive loop
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str, *, close: bool = False) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}}, close=close)

    def _authorized(self) -> bool:
        token = self.server.state.token
        if token is None or self.headers.get("Authorization") == f"Bearer {token}":
            return True
        # The request body is not read on this path: close the connection, or its bytes would be
        # taken for the next request on a kept-alive connection.
        self._error(401, "unauthorized", "a bearer token is required", close=True)
        return False

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        return self.rfile.read(length) if length > 0 else b""

    # --- endpoints ---------------------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        state = self.server.state
        if self.path == "/v1/info":
            self._send_json(
                200,
                {
                    "protocol": "voicemate-stt",
                    "version": 1,
                    "name": "Reference duration provider",
                    "languages": list(state.languages),
                    "sample_rates": list(SAMPLE_RATES),
                    "formats": ["pcm_s16le"],
                    "max_seconds": MAX_SECONDS,
                    "features": [],
                },
            )
        elif self.path == "/v1/health":
            loading = time.monotonic() < state.loading_until
            self._send_json(200, {"status": "loading" if loading else "ok"})
        else:
            self._error(404, "bad_request", "unknown endpoint")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        body = self._body()
        state = self.server.state
        if self.path == "/v1/transcribe":
            self._transcribe(body)
        elif self.path == "/v1/warmup":
            state.warmups += 1
            self._send_json(200, {})
        else:
            self._error(404, "bad_request", "unknown endpoint")

    def _transcribe(self, pcm: bytes) -> None:
        state = self.server.state
        if time.monotonic() < state.loading_until:
            return self._error(503, "not_ready", "the models are still loading")
        headers = self.headers
        try:
            rate = int(headers.get("X-VoiceMate-Sample-Rate") or 0)
            channels = int(headers.get("X-VoiceMate-Channels") or 1)
        except ValueError:
            return self._error(400, "bad_request", "X-VoiceMate-Sample-Rate and X-VoiceMate-Channels are numbers")
        audio_format = headers.get("X-VoiceMate-Format") or "pcm_s16le"
        language = headers.get("X-VoiceMate-Language")
        options: object = {}
        raw_options = headers.get("X-VoiceMate-Options")
        if raw_options:
            try:
                options = json.loads(raw_options)
            except ValueError:
                options = None
            if not isinstance(options, dict):
                return self._error(400, "bad_request", "X-VoiceMate-Options must be a JSON object")
        if audio_format != "pcm_s16le" or channels != 1 or rate not in SAMPLE_RATES:
            return self._error(400, "unsupported_format", f"mono pcm_s16le at {SAMPLE_RATES[0]} Hz only")
        if len(pcm) % 2:
            return self._error(400, "bad_request", "pcm_s16le has two bytes per sample")
        if language is not None and (
            not _LANGUAGE.fullmatch(language) or (state.languages and language not in state.languages)
        ):
            return self._error(400, "unsupported_language", f"unsupported language {language!r}")
        if len(pcm) / 2 / rate > MAX_SECONDS:
            return self._error(400, "audio_too_long", f"at most {MAX_SECONDS} seconds")
        with state.lock:
            if state.busy:
                return self._error(409, "busy", "one request at a time")
            state.busy = True
            state.requests.append(
                {
                    "request_id": headers.get("X-VoiceMate-Request-Id"),
                    "sample_rate": rate,
                    "language": language,
                    "options": options,
                    "bytes": len(pcm),
                }
            )
        try:
            answer: dict[str, Any] = {"text": describe(pcm, rate)}
            if language:
                answer["language"] = language
            self._send_json(200, answer)
        finally:
            with state.lock:
                state.busy = False


class ProviderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: ProviderState, verbose: bool = False) -> None:
        super().__init__(address, Handler)
        self.state = state
        self.verbose = verbose


def make_server(
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    token: str | None = None,
    languages: Sequence[str] = (),
    loading_seconds: float = 0.0,
    verbose: bool = False,
) -> ProviderServer:
    """A server bound to `host:port` (port 0 picks a free one: read `server.server_port`)."""
    state = ProviderState(token=token, languages=tuple(languages), loading_until=time.monotonic() + loading_seconds)
    return ProviderServer((host, port), state, verbose)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reference VoiceMate STT provider (answers the audio duration).")
    parser.add_argument("--host", default="127.0.0.1", help="listen address (keep it on loopback)")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token", default=None, help="require this bearer token")
    parser.add_argument("--languages", nargs="*", default=[], help="ISO 639-1 codes to accept (default: any)")
    parser.add_argument("--loading-seconds", type=float, default=0.0, help="report 'loading' for this long")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)
    server = make_server(
        args.host,
        args.port,
        token=args.token,
        languages=args.languages,
        loading_seconds=args.loading_seconds,
        verbose=args.verbose,
    )
    print(f"Reference STT provider listening on http://{args.host}:{server.server_port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
