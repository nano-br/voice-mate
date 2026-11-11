"""The external STT provider client (app/protocol/stt_provider.py), its conformance check and
the reference provider, against each other on a free loopback port. Standard library only, so
the companion suite runs it too."""

from __future__ import annotations

import json
import threading
import wave
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.protocol import provider_http, tts_provider
from app.protocol.stt_provider import (
    ProviderError,
    SttProviderClient,
    options_header,
    parse_info,
    parse_transcription,
)
from tools import stt_provider_check
from tools.examples.stt_provider_reference import ProviderServer, make_server


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def provider() -> Iterator[ProviderServer]:
    server = make_server()
    _serve(server)
    yield server
    server.shutdown()
    server.server_close()


def _url(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


# --- pure helpers ----------------------------------------------------------------------------------------


def test_the_shared_pieces_are_the_same_for_both_protocols() -> None:
    assert tts_provider.ProviderError is provider_http.ProviderError is ProviderError
    assert tts_provider.parse_url is provider_http.parse_url


def test_parse_info_checks_protocol_version_rates_and_formats() -> None:
    good = {"protocol": "voicemate-stt", "version": 1, "sample_rates": [16000], "formats": ["pcm_s16le"]}
    info = parse_info(good)
    assert info.name == "STT provider" and info.languages == () and info.max_seconds is None
    assert info.accepts_language("pt") and info.accepts_language(None)
    assert info.sample_rate_for(16000) == 16000
    for bad in (
        {**good, "protocol": "voicemate-tts"},
        {**good, "version": 2},
        {**good, "version": True},
        {**good, "sample_rates": []},
        {**good, "sample_rates": [0, True, "16000"]},
        {**good, "formats": ["pcm_f32le"]},
    ):
        with pytest.raises(ProviderError) as caught:
            parse_info(bad)
        assert caught.value.kind == "protocol"


def test_parse_info_reads_languages_rates_and_the_optional_limit() -> None:
    info = parse_info(
        {
            "protocol": "voicemate-stt",
            "version": 1,
            "name": "Mine",
            "languages": ["pt", "en", 3],
            "sample_rates": [24000, 48000],
            "formats": ["pcm_s16le"],
            "max_seconds": 600,
            "features": ["future-thing"],
        }
    )
    assert (info.name, info.languages, info.max_seconds) == ("Mine", ("pt", "en"), 600.0)
    assert info.accepts_language("pt") and not info.accepts_language("ru")
    assert info.sample_rate_for(16000) == 24000  # not listed: the provider's first rate
    assert info.supports("future-thing") and not info.supports("x")
    assert parse_info({**_minimal(), "max_seconds": 0}).max_seconds is None
    assert parse_info({**_minimal(), "max_seconds": True}).max_seconds is None


def _minimal() -> dict[str, object]:
    return {"protocol": "voicemate-stt", "version": 1, "sample_rates": [16000], "formats": ["pcm_s16le"]}


def test_parse_transcription() -> None:
    assert parse_transcription({"text": " hello ", "language": "en"}).text == "hello"
    assert parse_transcription({"text": ""}).language is None
    with pytest.raises(ProviderError) as caught:
        parse_transcription({"words": []})
    assert caught.value.kind == "protocol"


def test_options_header_is_compact_ascii_json() -> None:
    header = options_header({"prompt": "olá", "beam_size": 5})
    assert header == '{"prompt":"ol\\u00e1","beam_size":5}'
    assert header.isascii() and json.loads(header) == {"prompt": "olá", "beam_size": 5}


# --- the client against the reference provider -----------------------------------------------------------


def test_info_health_and_warmup(provider: ProviderServer) -> None:
    client = SttProviderClient(_url(provider))
    info = client.info()
    assert info.name == "Reference duration provider" and info.sample_rates == (16000,)
    assert client.health() == "ok"
    client.warmup()
    assert provider.state.warmups == 1


def test_transcribe_sends_pcm_with_the_voicemate_headers(provider: ProviderServer) -> None:
    client = SttProviderClient(_url(provider))
    result = client.transcribe(
        stt_provider_check.tone(16000, 2.0), sample_rate=16000, language="pt", options={"beam_size": 5}
    )
    assert result.text == "I heard 2.0 seconds of audio." and result.language == "pt"
    sent = provider.state.requests[0]
    assert (sent["sample_rate"], sent["language"], sent["options"], sent["bytes"]) == (
        16000,
        "pt",
        {"beam_size": 5},
        64000,
    )
    assert len(sent["request_id"]) >= 8


def test_no_language_and_no_options_send_no_header(provider: ProviderServer) -> None:
    result = SttProviderClient(_url(provider)).transcribe(stt_provider_check.silence(16000), sample_rate=16000)
    assert result.text == "" and result.language is None
    assert provider.state.requests[0]["language"] is None and provider.state.requests[0]["options"] == {}


def test_errors_carry_the_provider_code(provider: ProviderServer) -> None:
    client = SttProviderClient(_url(provider))
    with pytest.raises(ProviderError) as caught:
        client.transcribe(b"\0\0", sample_rate=8000)
    assert (caught.value.kind, caught.value.code) == ("rejected", "unsupported_format")
    with pytest.raises(ProviderError) as caught:
        client.transcribe(b"\0\0", sample_rate=16000, language="english")
    assert caught.value.code == "unsupported_language"


def test_a_url_with_a_path_prefix_reaches_the_provider_under_it() -> None:
    """One service may serve both protocols under different prefixes of the same host and port."""
    seen: list[str] = []

    class Prefixed(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.path)
            body = json.dumps({**_minimal(), "name": "Prefixed"}).encode()
            self.send_response(200 if self.path == "/stt/v1/info" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Prefixed)
    _serve(server)
    try:
        assert SttProviderClient(f"{_url(server)}/stt/").info().name == "Prefixed"
        assert seen == ["/stt/v1/info"]
    finally:
        server.shutdown()
        server.server_close()


def test_token_is_required_and_never_in_the_error() -> None:
    server = make_server(token="s3cret")
    _serve(server)
    try:
        with pytest.raises(ProviderError) as caught:
            SttProviderClient(_url(server)).transcribe(b"\0\0", sample_rate=16000)
        assert caught.value.kind == "unauthorized" and "s3cret" not in str(caught.value)
        assert SttProviderClient(_url(server), "s3cret").info().name
    finally:
        server.shutdown()
        server.server_close()


def test_a_loading_provider_says_so_and_refuses_to_transcribe() -> None:
    server = make_server(loading_seconds=30)
    _serve(server)
    try:
        client = SttProviderClient(_url(server))
        assert client.health() == "loading"
        with pytest.raises(ProviderError) as caught:
            client.transcribe(b"\0\0", sample_rate=16000)
        assert caught.value.kind == "not_ready"
    finally:
        server.shutdown()
        server.server_close()


def test_a_provider_that_is_down_is_unreachable() -> None:
    server = make_server()
    port = server.server_port
    server.server_close()
    with pytest.raises(ProviderError) as caught:
        SttProviderClient(f"http://127.0.0.1:{port}", connect_timeout=1.0).info()
    assert caught.value.kind == "unreachable"


# --- the conformance check -------------------------------------------------------------------------------


def test_check_passes_on_the_reference_provider(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    recording = tmp_path / "me.wav"
    with wave.open(str(recording), "wb") as wav:  # stereo at 44.1 kHz: the check resamples it
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        mono = stt_provider_check.tone(44100, 1.0)
        wav.writeframes(b"".join(mono[i : i + 2] * 2 for i in range(0, len(mono), 2)))
    server = make_server(languages=["pt", "en"])
    _serve(server)
    try:
        assert stt_provider_check.run(_url(server), None, recording, "pt", {"beam_size": 5}) == 0
    finally:
        server.shutdown()
        server.server_close()
    text = capsys.readouterr().out
    assert "PASSED" in text and "  FAIL  " not in text and "  warn  " not in text
    assert "I heard 1.0 seconds of audio." in text and "refused with 400 unsupported_format" in text


def test_check_fails_when_nothing_listens(capsys: pytest.CaptureFixture[str]) -> None:
    server = make_server()
    port = server.server_port
    server.server_close()
    assert stt_provider_check.run(f"http://127.0.0.1:{port}", None) == 1
    assert "FAIL" in capsys.readouterr().out


class _Lenient(BaseHTTPRequestHandler):
    """A provider that accepts anything and hears words in silence."""

    protocol_version = "HTTP/1.1"

    def _json(self, body: dict[str, object]) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self._json(_minimal() if self.path == "/v1/info" else {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self._json({"text": "thank you"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None


def test_check_flags_a_provider_that_accepts_anything(capsys: pytest.CaptureFixture[str]) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Lenient)
    _serve(server)
    try:
        assert stt_provider_check.run(_url(server), None) == 1
    finally:
        server.shutdown()
        server.server_close()
    out = capsys.readouterr().out
    assert "silence was heard as 'thank you'" in out and "was accepted" in out
