"""A reference TTS provider for VoiceMate's external provider protocol, version 1.

It implements every endpoint of docs/tts-provider-protocol.md with the standard library only
and "speaks" a short sine tone for each word, so the protocol, VoiceMate's client and
`make tts-provider-check` can be tried without any real text-to-speech. Read it as the
smallest working example when writing your own provider.

    python -m tools.examples.tts_provider_reference                   # http://127.0.0.1:8765
    python -m tools.examples.tts_provider_reference --port 9000 --token secret
    python -m tools.examples.tts_provider_reference --loading-seconds 5 --expression-tags

Then, in another terminal: make tts-provider-check URL=http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Final

SAMPLE_RATE: Final = 24000
CHUNK_MS: Final = 20  # audio is produced, checked for cancellation and sent in 20 ms pieces
MAX_CHARS: Final = 2000
VOICES: Final = (
    {"id": "low", "name": "Low tone", "languages": ["en", "pt-BR"], "default": False},
    {"id": "mid", "name": "Middle tone", "languages": ["en", "pt-BR"], "default": True},
    {"id": "high", "name": "High tone", "languages": ["en", "pt-BR"], "default": False},
)
BASE_HZ: Final = {"low": 220.0, "mid": 330.0, "high": 495.0}
_TAG: Final = re.compile(r"\[/?(?:laugh|chuckle|sigh|pause|whisper)\]", re.IGNORECASE)


@dataclass
class ProviderState:
    """What the server knows; tests read `requests` and `cancelled`."""

    token: str | None = None
    expression_tags: bool = False
    pace: float = 1.0  # 1.0 = real time, 0.0 = as fast as possible
    loading_until: float = 0.0  # monotonic time; /v1/health says "loading" before it
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: dict[str, threading.Event] = field(default_factory=dict)  # request_id -> cancelled
    requests: list[dict[str, Any]] = field(default_factory=list)  # every /v1/speak body received
    cancelled: list[str] = field(default_factory=list)  # request ids that /v1/cancel stopped
    warmups: int = 0


def tone_chunks(text: str, voice: str, sample_format: str) -> Iterator[bytes]:
    """The test tone for `text`: one beep per word, higher for longer words, 20 ms at a time."""
    base = BASE_HZ.get(voice, BASE_HZ["mid"])
    per_chunk = SAMPLE_RATE * CHUNK_MS // 1000
    for word in text.split():
        hz = base + 40.0 * (sum(map(ord, word)) % 6)
        beep = int(SAMPLE_RATE * (0.10 + 0.02 * min(len(word), 8)))
        for start in range(0, beep + SAMPLE_RATE // 25, per_chunk):  # the tail is 40 ms of silence
            samples = []
            for n in range(start, start + per_chunk):
                if n >= beep:
                    samples.append(0.0)
                    continue
                fade = min(1.0, n / 240, (beep - n) / 240)
                samples.append(0.3 * fade * math.sin(2 * math.pi * hz * n / SAMPLE_RATE))
            if sample_format == "pcm_f32le":
                yield struct.pack(f"<{len(samples)}f", *samples)
            else:
                yield struct.pack(f"<{len(samples)}h", *(int(s * 32767) for s in samples))


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

    def _body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data
        self._error(400, "bad_request", "the body must be a JSON object")
        return None

    # --- endpoints ---------------------------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        state = self.server.state
        if self.path == "/v1/info":
            features = ["streaming"] + (["expression_tags"] if state.expression_tags else [])
            self._send_json(
                200,
                {
                    "protocol": "voicemate-tts",
                    "version": 1,
                    "name": "Reference test-tone provider",
                    "voices": list(VOICES),
                    "sample_rate": SAMPLE_RATE,
                    "formats": ["pcm_s16le", "pcm_f32le"],
                    "max_chars": MAX_CHARS,
                    "features": features,
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
        if body is None:
            return
        state = self.server.state
        if self.path == "/v1/speak":
            self._speak(body)
        elif self.path == "/v1/cancel":
            request_id = body.get("request_id")
            with state.lock:
                event = state.active.get(request_id) if isinstance(request_id, str) else None
                if event is not None:
                    state.cancelled.append(str(request_id))
            if event is not None:
                event.set()
            self._send_json(200, {})
        elif self.path == "/v1/warmup":
            state.warmups += 1
            self._send_json(200, {})
        else:
            self._error(404, "bad_request", "unknown endpoint")

    def _speak(self, body: dict[str, Any]) -> None:
        state = self.server.state
        if time.monotonic() < state.loading_until:
            return self._error(503, "not_ready", "the models are still loading")
        request_id, text = body.get("request_id"), body.get("text")
        voice = body.get("voice") or "mid"
        sample_format = body.get("format") or "pcm_s16le"
        if not isinstance(request_id, str) or not request_id or not isinstance(text, str) or not text.strip():
            return self._error(400, "bad_request", "request_id and text are required")
        if len(text) > MAX_CHARS:
            return self._error(400, "text_too_long", f"at most {MAX_CHARS} characters")
        if voice not in BASE_HZ:
            return self._error(400, "unknown_voice", f"unknown voice {voice!r}")
        if sample_format not in ("pcm_s16le", "pcm_f32le"):
            return self._error(400, "unsupported_format", f"unsupported format {sample_format!r}")
        cancelled = threading.Event()
        with state.lock:
            if state.active:
                return self._error(409, "busy", "one request at a time")
            state.active[request_id] = cancelled
            state.requests.append(body)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("X-VoiceMate-Sample-Rate", str(SAMPLE_RATE))
            self.send_header("X-VoiceMate-Channels", "1")
            self.send_header("X-VoiceMate-Format", sample_format)
            self.end_headers()
            # Providers without the expression_tags feature get plain text from VoiceMate; this
            # one strips the tags itself if they arrive anyway, instead of beeping at them.
            spoken = text if state.expression_tags else _TAG.sub(" ", text)
            # Pace against a schedule, not with a fixed sleep per chunk: that drifts (a Windows
            # timer tick is 15 ms) and would make the "voice" slower than real time.
            started = time.monotonic()
            for index, chunk in enumerate(tone_chunks(spoken, str(voice), sample_format)):
                if cancelled.is_set():
                    break
                self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
                self.wfile.flush()
                due = started + (index + 1) * CHUNK_MS / 1000 * state.pace
                if state.pace > 0 and cancelled.wait(max(0.0, due - time.monotonic())):
                    break
            if not cancelled.is_set():
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the client closed the connection: that is a cancel too
        finally:
            with state.lock:
                state.active.pop(request_id, None)
            self.close_connection = True


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
    expression_tags: bool = False,
    pace: float = 1.0,
    loading_seconds: float = 0.0,
    verbose: bool = False,
) -> ProviderServer:
    """A server bound to `host:port` (port 0 picks a free one: read `server.server_port`)."""
    state = ProviderState(
        token=token, expression_tags=expression_tags, pace=pace, loading_until=time.monotonic() + loading_seconds
    )
    return ProviderServer((host, port), state, verbose)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reference VoiceMate TTS provider (test tone per word).")
    parser.add_argument("--host", default="127.0.0.1", help="listen address (keep it on loopback)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=None, help="require this bearer token")
    parser.add_argument("--expression-tags", action="store_true", help="advertise the expression_tags feature")
    parser.add_argument("--pace", type=float, default=1.0, help="1.0 = real time, 0 = as fast as possible")
    parser.add_argument("--loading-seconds", type=float, default=0.0, help="report 'loading' for this long")
    parser.add_argument("--verbose", action="store_true", help="log every request")
    args = parser.parse_args(argv)
    server = make_server(
        args.host,
        args.port,
        token=args.token,
        expression_tags=args.expression_tags,
        pace=args.pace,
        loading_seconds=args.loading_seconds,
        verbose=args.verbose,
    )
    print(f"Reference TTS provider listening on http://{args.host}:{server.server_port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
