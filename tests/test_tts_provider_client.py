"""The external TTS provider client (app/protocol/tts_provider.py), its conformance check and
the reference provider, against each other on a free loopback port. Standard library only, so
the companion suite runs it too."""

from __future__ import annotations

import json
import threading
import time
import wave
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.protocol.tts_provider import (
    ProviderClient,
    ProviderError,
    SpeechStream,
    language_for_provider,
    parse_info,
    parse_url,
    split_text,
    strip_expression_tags,
    url_is_valid,
    value_is_safe,
)
from tools import tts_provider_check
from tools.examples.tts_provider_reference import ProviderServer, make_server


def _serve(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def provider() -> Iterator[ProviderServer]:
    server = make_server(pace=0.0)
    _serve(server)
    yield server
    server.shutdown()
    server.server_close()


def _url(server: ThreadingHTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


def _pcm(client: ProviderClient, text: str, **kwargs: object) -> tuple[bytes, int]:
    stream = client.speak(text, **kwargs)  # type: ignore[arg-type]
    try:
        return b"".join(stream.chunks()), stream.sample_rate
    finally:
        stream.close()


# --- pure helpers ----------------------------------------------------------------------------------------


def test_parse_url_accepts_http_and_https_with_a_prefix() -> None:
    endpoint = parse_url("http://127.0.0.1:8765/")
    assert (endpoint.secure, endpoint.host, endpoint.port, endpoint.prefix) == (False, "127.0.0.1", 8765, "")
    endpoint = parse_url("https://tts.example.test/api/")
    assert (endpoint.secure, endpoint.port, endpoint.prefix) == (True, 443, "/api")


@pytest.mark.parametrize("url", ["", "127.0.0.1:8765", "ftp://host", "http://", "http://u:p@host", "http://h/?q=1"])
def test_parse_url_rejects_anything_else(url: str) -> None:
    with pytest.raises(ValueError):
        parse_url(url)
    assert not url_is_valid(url)


def test_value_is_safe_keeps_command_line_metacharacters_out() -> None:
    assert value_is_safe("http://127.0.0.1:8765") and value_is_safe("pt-BR_narrator.2") and value_is_safe("")
    for bad in ('a"b', "a b", "a$b", "a`b", "a\\b", "a\nb", "a;b", "a&b"):
        assert not value_is_safe(bad)


def test_strip_expression_tags() -> None:
    assert strip_expression_tags("Hello [laugh] world [whisper]psst[/whisper]!") == "Hello world psst !"
    assert strip_expression_tags("[sigh] Fine.") == "Fine."
    assert strip_expression_tags("Keep [brackets] that are not tags.") == "Keep [brackets] that are not tags."


def test_split_text_cuts_at_sentence_ends_and_respects_the_limit() -> None:
    text = "First sentence here. Second sentence here. Third one."
    pieces = split_text(text, 25)
    assert pieces == ["First sentence here.", "Second sentence here.", "Third one."]
    assert all(len(piece) <= 33 for piece in split_text("word " * 100, 33))
    assert split_text("short", 100) == ["short"]
    assert split_text("x" * 50, 20) == ["x" * 20, "x" * 20, "x" * 10]


def test_language_for_provider() -> None:
    assert language_for_provider("pt") == "pt-BR"
    assert language_for_provider("en") == "en"
    assert language_for_provider("auto") is None


def test_parse_info_checks_protocol_version_and_formats() -> None:
    good = {"protocol": "voicemate-tts", "version": 1, "sample_rate": 22050, "formats": ["pcm_s16le"]}
    info = parse_info(good)
    assert info.sample_rate == 22050 and info.voices == () and info.max_chars is None and not info.supports("x")
    for bad in (
        {**good, "protocol": "other"},
        {**good, "version": 2},
        {**good, "version": True},
        {**good, "sample_rate": 0},
        {**good, "formats": ["pcm_f32le"]},
    ):
        with pytest.raises(ProviderError) as caught:
            parse_info(bad)
        assert caught.value.kind == "protocol"


# --- the client against the reference provider -----------------------------------------------------------


def test_info_health_and_warmup(provider: ProviderServer) -> None:
    client = ProviderClient(_url(provider))
    info = client.info()
    assert info.name and info.sample_rate == 24000 and info.supports("streaming")
    assert [voice.id for voice in info.voices] == ["low", "mid", "high"]
    assert [voice.id for voice in info.voices if voice.default] == ["mid"]
    assert client.health() == "ok"
    client.warmup()
    assert provider.state.warmups == 1


def test_speak_streams_pcm_with_the_voicemate_headers(provider: ProviderServer) -> None:
    client = ProviderClient(_url(provider))
    data, rate = _pcm(client, "Hello there world", voice="high", language="en", options={"speed": 1.2})
    assert rate == 24000 and len(data) > 24000 and len(data) % 2 == 0
    sent = provider.state.requests[0]
    assert (sent["text"], sent["voice"], sent["language"], sent["format"]) == (
        "Hello there world",
        "high",
        "en",
        "pcm_s16le",
    )
    assert sent["options"] == {"speed": 1.2} and len(sent["request_id"]) >= 8


def test_speak_float_format_and_default_voice(provider: ProviderServer) -> None:
    client = ProviderClient(_url(provider))
    data, _rate = _pcm(client, "Hello", audio_format="pcm_f32le")
    assert len(data) % 4 == 0
    assert provider.state.requests[0]["voice"] is None


def test_unknown_voice_and_too_long_text_are_rejected_with_the_provider_code(provider: ProviderServer) -> None:
    client = ProviderClient(_url(provider))
    with pytest.raises(ProviderError) as caught:
        client.speak("Hello", voice="nope")
    assert (caught.value.kind, caught.value.code) == ("rejected", "unknown_voice")
    with pytest.raises(ProviderError) as caught:
        client.speak("x " * 2000)
    assert caught.value.code == "text_too_long"


def test_a_second_request_while_one_runs_is_busy() -> None:
    server = make_server(pace=1.0)
    _serve(server)
    try:
        client = ProviderClient(_url(server))
        first = client.speak("one two three four five six")
        with pytest.raises(ProviderError) as caught:
            client.speak("second")
        assert caught.value.kind == "busy"
        first.close()
    finally:
        server.shutdown()
        server.server_close()


def test_cancel_stops_the_stream_early() -> None:
    server = make_server(pace=1.0)
    _serve(server)
    try:
        client = ProviderClient(_url(server))
        stream = client.speak("word " * 60)
        chunks = stream.chunks()
        assert next(chunks)
        client.cancel(stream.request_id)
        received = sum(len(chunk) for chunk in _drain(stream))
        assert received < 24000 * 2 * 2  # far less than the ~12 s of audio the text would make
        assert stream.request_id in server.state.cancelled
        stream.close()
    finally:
        server.shutdown()
        server.server_close()


def _drain(stream: object) -> Iterator[bytes]:
    try:
        yield from stream.chunks()  # type: ignore[attr-defined]
    except ProviderError:
        return  # a provider may cut the connection instead of ending the chunked body


def test_closing_the_connection_cancels_too() -> None:
    server = make_server(pace=1.0)
    _serve(server)
    try:
        client = ProviderClient(_url(server))
        stream = client.speak("word " * 60)
        assert next(stream.chunks())
        stream.close()
        deadline = threading.Event()
        for _ in range(100):
            if not server.state.active:
                break
            deadline.wait(0.02)
        assert not server.state.active
    finally:
        server.shutdown()
        server.server_close()


class _Stalling(BaseHTTPRequestHandler):
    """Sends the headers and a few bytes, then stalls (synthesis between two chunks)."""

    chunked = True
    hung_up_after: list[float] = []  # seconds the stalled handler waited before it saw EOF

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("X-VoiceMate-Sample-Rate", "24000")
        self.send_header("Connection", "close")  # like the engine's own stream: will_close even when chunked
        if self.chunked:
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        silence = bytes(4)
        self.wfile.write(b"4" + b"\r\n" + silence + b"\r\n" if self.chunked else silence)
        self.wfile.flush()
        started = time.monotonic()
        self.connection.settimeout(3)
        try:
            if self.connection.recv(1) == b"":  # the client hung up
                self.hung_up_after.append(time.monotonic() - started)
        except OSError:
            pass


@pytest.mark.parametrize("chunked", [True, False], ids=["chunked", "close-delimited"])
def test_close_does_not_wait_for_a_stalled_read(chunked: bool) -> None:
    hung_up: list[float] = []
    handler = type(
        "H",
        (_Stalling,),
        {"chunked": chunked, "protocol_version": "HTTP/1.1" if chunked else "HTTP/1.0", "hung_up_after": hung_up},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    _serve(server)
    try:
        stream = ProviderClient(_url(server)).speak("hi")
        got: list[int] = []
        errors: list[BaseException] = []

        def read() -> None:
            try:
                got.extend(len(chunk) for chunk in stream.chunks())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        time.sleep(0.3)
        started = time.monotonic()
        stream.close()
        assert time.monotonic() - started < 0.2
        reader.join(5)
        assert not reader.is_alive()
        deadline = time.monotonic() + 1.0
        while not hung_up and time.monotonic() < deadline:
            time.sleep(0.02)
        assert hung_up and hung_up[0] < 1.0  # the provider sees the hang-up, not a timeout
        assert got == [4]
        assert errors == []  # the reader ends quietly after an on-purpose close
    finally:
        server.shutdown()
        server.server_close()


def test_a_refused_request_closes_the_connection_instead_of_reusing_it() -> None:
    import http.client

    server = make_server(token="s3cret", pace=0.0)
    _serve(server)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        body = json.dumps({"request_id": "r", "text": "hello"})
        connection.request("POST", "/v1/speak", body, {"Content-Type": "application/json"})
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        assert response.getheader("Connection") == "close"  # the unread body must not become a request
        connection.close()
    finally:
        server.shutdown()
        server.server_close()


def test_token_is_required_and_sent_as_a_bearer_header() -> None:
    server = make_server(token="s3cret", pace=0.0)
    _serve(server)
    try:
        with pytest.raises(ProviderError) as caught:
            ProviderClient(_url(server)).info()
        assert caught.value.kind == "unauthorized"
        assert "s3cret" not in str(caught.value)
        assert ProviderClient(_url(server), "s3cret").info().name
    finally:
        server.shutdown()
        server.server_close()


def test_a_provider_that_is_down_is_unreachable() -> None:
    server = make_server()
    port = server.server_port
    server.server_close()
    with pytest.raises(ProviderError) as caught:
        ProviderClient(f"http://127.0.0.1:{port}", connect_timeout=1.0).info()
    assert caught.value.kind == "unreachable"


class _Other(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = json.dumps({"hello": "world"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None


def test_another_kind_of_service_is_a_protocol_error() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Other)
    _serve(server)
    try:
        with pytest.raises(ProviderError) as caught:
            ProviderClient(_url(server)).info()
        assert caught.value.kind == "protocol"
    finally:
        server.shutdown()
        server.server_close()


# --- the conformance check -------------------------------------------------------------------------------


def test_check_passes_on_the_reference_provider(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    server = make_server(pace=0.1)
    _serve(server)
    try:
        out = tmp_path / "speech.wav"
        assert tts_provider_check.run(_url(server), None, None, tts_provider_check.SENTENCE, out) == 0
    finally:
        server.shutdown()
        server.server_close()
    text = capsys.readouterr().out
    assert "PASSED" in text and "time to first byte" in text and "FAIL" not in text
    with wave.open(str(out), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        assert wav.getnframes() > 0


def test_check_fails_when_nothing_listens(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    server = make_server()
    port = server.server_port
    server.server_close()
    assert tts_provider_check.run(f"http://127.0.0.1:{port}", None, None, "Hello.", tmp_path / "x.wav") == 1
    assert "FAIL" in capsys.readouterr().out


def test_check_fails_on_a_service_that_is_not_a_provider(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Other)
    _serve(server)
    try:
        assert tts_provider_check.run(_url(server), None, None, "Hello.", tmp_path / "x.wav") == 1
    finally:
        server.shutdown()
        server.server_close()
    assert "not a VoiceMate TTS provider" in capsys.readouterr().out


def test_check_hangup_step_passes_when_closing_the_connection_frees_the_provider() -> None:
    server = make_server(pace=1.0)
    _serve(server)
    try:
        client = ProviderClient(_url(server))
        report = tts_provider_check.Report()
        assert tts_provider_check.check_hangup(client, client.info(), None, report)
        assert report.failures == 0
    finally:
        server.shutdown()
        server.server_close()


def test_check_hangup_step_fails_when_the_provider_stays_busy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    server = make_server(pace=1.0)
    _serve(server)
    streams: list[object] = []
    try:
        client = ProviderClient(_url(server))
        info = client.info()
        real_close = SpeechStream.close

        def keep_the_connection(self: SpeechStream) -> None:
            streams.append(self)  # "closed" for the caller, but the provider never sees a hang-up
            self._closed.set()

        monkeypatch.setattr(SpeechStream, "close", keep_the_connection)
        monkeypatch.setattr(tts_provider_check, "HANGUP_FAIL_S", 0.3)
        report = tts_provider_check.Report()
        assert not tts_provider_check.check_hangup(client, info, None, report)
        assert report.failures == 1 and "still busy" in capsys.readouterr().out
        for stream in streams:
            real_close(stream)  # type: ignore[arg-type]
    finally:
        server.shutdown()
        server.server_close()
