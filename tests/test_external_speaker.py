"""ExternalSpeaker against the reference provider on a free loopback port (no audio device:
a fake player records what would be played), and the engine-side configuration of the engine."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from app.cli.args import parse_args
from app.cli.config_builder import build_config
from app.core.config import TTSConfig
from app.features import tts as tts_feature
from app.features.tts import external_speaker
from app.features.tts.external_speaker import ExternalSpeaker
from app.protocol.tts_provider import ProviderError
from app.setup.persisted_config import PersistedConfig, load_persisted, save_persisted
from tools.examples.tts_provider_reference import ProviderServer, make_server


class FakePlayer:
    """An AudioSink that records instead of playing."""

    def __init__(self) -> None:
        self.rates: list[int] = []
        self.chunks: list[NDArray[np.float32]] = []
        self.aborts = 0
        self.closed = False

    def ensure_started(self, sample_rate: int) -> None:
        self.rates.append(sample_rate)

    def start(self, sample_rate: int) -> None:
        self.rates.append(sample_rate)

    def feed(self, chunk: NDArray[np.float32]) -> None:
        self.chunks.append(chunk)

    def drain(self, timeout: float | None = 60.0) -> bool:
        return True

    def abort(self) -> None:
        self.aborts += 1

    def close(self) -> None:
        self.closed = True

    @property
    def samples(self) -> int:
        return sum(chunk.size for chunk in self.chunks)


def _serve(server: ProviderServer) -> None:
    threading.Thread(target=server.serve_forever, daemon=True).start()


@pytest.fixture
def started() -> Iterator[list[ProviderServer]]:
    servers: list[ProviderServer] = []
    yield servers
    for server in servers:
        server.shutdown()
        server.server_close()


def _provider(started: list[ProviderServer], **kwargs: object) -> ProviderServer:
    server = make_server(**kwargs)  # type: ignore[arg-type]
    started.append(server)
    _serve(server)
    return server


def _speaker(server: ProviderServer | int, player: FakePlayer, **config: object) -> ExternalSpeaker:
    port = server if isinstance(server, int) else server.server_port
    settings = TTSConfig(engine="external", external_url=f"http://127.0.0.1:{port}", **config)  # type: ignore[arg-type]
    return ExternalSpeaker(settings, player=player)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_speak_streams_the_pcm_into_the_player(started: list[ProviderServer]) -> None:
    server, player = _provider(started, pace=0.0), FakePlayer()
    speaker = _speaker(server, player)
    assert speaker.is_active()
    speaker.speak("Hello there world")
    assert player.rates == [24000]
    assert player.samples > 24000 * 0.5
    assert all(chunk.dtype == np.float32 and chunk.ndim == 1 for chunk in player.chunks)
    assert all(float(np.abs(chunk).max()) <= 1.0 for chunk in player.chunks)
    sent = server.state.requests[0]
    assert (sent["text"], sent["voice"], sent["language"], sent["format"]) == (
        "Hello there world",
        None,
        "pt-BR",
        "pcm_s16le",
    )
    assert speaker.wait_done(1.0)


def test_voice_and_options_are_passed_through(started: list[ProviderServer]) -> None:
    server, player = _provider(started, pace=0.0), FakePlayer()
    speaker = _speaker(server, player, external_voice="high", external_options={"speed": 1.25, "style": "calm"})
    speaker.speak("Hello")
    sent = server.state.requests[0]
    assert sent["voice"] == "high" and sent["options"] == {"speed": 1.25, "style": "calm"}


def test_expression_tags_are_stripped_unless_the_provider_supports_them(started: list[ProviderServer]) -> None:
    plain = _provider(started, pace=0.0)
    tagged = _provider(started, pace=0.0, expression_tags=True)
    text = "Well [laugh] that is [whisper]a secret[/whisper] indeed [pause] ok"
    _speaker(plain, FakePlayer()).speak(text)
    _speaker(tagged, FakePlayer()).speak(text)
    assert plain.state.requests[0]["text"] == "Well that is a secret indeed ok"
    assert tagged.state.requests[0]["text"] == text


def test_a_text_that_is_only_tags_sends_nothing(started: list[ProviderServer]) -> None:
    server = _provider(started, pace=0.0)
    _speaker(server, FakePlayer()).speak("[laugh] [pause]")
    assert server.state.requests == []


def test_long_text_is_split_to_the_providers_max_chars_and_sent_in_order(started: list[ProviderServer]) -> None:
    server, player = _provider(started, pace=0.0), FakePlayer()
    sentence = "This sentence has a handful of words in it. "
    text = sentence * 60  # about 2600 characters, the reference accepts 2000
    _speaker(server, player).speak(text)
    sent = [request["text"] for request in server.state.requests]
    assert len(sent) == 2 and all(len(piece) <= 2000 for piece in sent)
    assert " ".join(sent).split() == text.split()


def test_requests_are_serialized(started: list[ProviderServer]) -> None:
    server, player = _provider(started, pace=0.05), FakePlayer()
    speaker = _speaker(server, player)
    threads = [threading.Thread(target=speaker.speak, args=(f"sentence number {n} here",)) for n in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(server.state.requests) == 3  # none was refused as busy


def test_stop_cancels_the_request_and_silences_the_player(started: list[ProviderServer]) -> None:
    server, player = _provider(started, pace=1.0), FakePlayer()
    speaker = _speaker(server, player)
    thread = threading.Thread(target=speaker.speak, args=("word " * 80,))
    thread.start()
    deadline = time.monotonic() + 5
    while player.samples == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert player.samples > 0
    fed = player.samples
    started_at = time.monotonic()
    speaker.stop()
    thread.join(3)
    assert not thread.is_alive()
    assert time.monotonic() - started_at < 1.0
    assert player.aborts >= 1
    assert len(server.state.cancelled) == 1
    assert player.samples - fed < 24000  # no more than a moment of audio after the stop
    deadline = time.monotonic() + 3
    while server.state.active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not server.state.active
    speaker.speak("Hello again")  # the speaker is usable after a stop
    assert len(server.state.requests) == 2


def test_stop_when_idle_is_harmless() -> None:
    speaker = _speaker(_free_port(), FakePlayer())
    speaker.stop()
    speaker.close()
    assert not speaker.is_active()


def test_warmup_checks_the_provider_and_calls_warmup(
    started: list[ProviderServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server = _provider(started, pace=0.0)
    _speaker(server, FakePlayer()).warmup()
    assert server.state.warmups == 1
    assert "Connected to 'Reference test-tone provider'" in capsys.readouterr().out


def test_a_loading_provider_is_waited_for(started: list[ProviderServer], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(external_speaker, "LOADING_POLL_SECONDS", 0.05)
    server, player = _provider(started, pace=0.0, loading_seconds=0.4), FakePlayer()
    _speaker(server, player).speak("Hello")
    assert player.samples > 0


def test_a_provider_that_is_down_is_reported_once_and_retried_later(capsys: pytest.CaptureFixture[str]) -> None:
    port, player = _free_port(), FakePlayer()
    speaker = _speaker(port, player)
    assert speaker.is_active()
    speaker.speak("Hello")  # must not raise
    speaker.speak("Hello again")
    assert not speaker.is_active()
    assert player.samples == 0
    err = capsys.readouterr().err
    assert err.count("not reachable") == 1 and f"127.0.0.1:{port}" in err
    speaker._down_until = 0.0  # the retry window has passed
    assert speaker.is_active()


def test_a_provider_that_comes_up_after_an_outage_is_used(started: list[ProviderServer]) -> None:
    port, player = _free_port(), FakePlayer()
    speaker = _speaker(port, player)
    speaker.speak("Hello")
    assert player.samples == 0
    server = make_server("127.0.0.1", port, pace=0.0)
    started.append(server)
    _serve(server)
    speaker._down_until = 0.0
    speaker.speak("Hello")
    assert player.samples > 0


def test_a_service_that_is_not_a_provider_switches_the_speaker_off(capsys: pytest.CaptureFixture[str]) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Other(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b'{"protocol": "something-else", "version": 1}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return None

    other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        player = FakePlayer()
        speaker = _speaker(other.server_port, player)
        speaker.speak("Hello")
        assert not speaker.is_active() and player.samples == 0
        assert "not a compatible TTS provider" in capsys.readouterr().err
    finally:
        other.shutdown()
        other.server_close()


def test_a_wrong_token_switches_the_speaker_off_and_is_never_printed(
    started: list[ProviderServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server = _provider(started, pace=0.0, token="the-real-token")
    player = FakePlayer()
    wrong = _speaker(server, player, external_token="a-wrong-token")
    wrong.speak("Hello")
    assert not wrong.is_active() and player.samples == 0
    right = _speaker(server, player, external_token="the-real-token")
    right.speak("Hello")
    assert player.samples > 0
    captured = capsys.readouterr()
    assert "the-real-token" not in captured.out + captured.err and "a-wrong-token" not in captured.out + captured.err
    assert "the-real-token" not in repr(TTSConfig(external_token="the-real-token"))


def test_an_unknown_voice_switches_the_speaker_off(
    started: list[ProviderServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server, player = _provider(started, pace=0.0), FakePlayer()
    speaker = _speaker(server, player, external_voice="no-such-voice")
    speaker.speak("Hello")
    assert not speaker.is_active()
    assert "unknown_voice" in capsys.readouterr().err


# --- configuration -----------------------------------------------------------------------------------------


def test_external_needs_no_packages_and_builds_the_external_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tts_feature.is_available("external") is True
    monkeypatch.setattr(external_speaker, "create_audio_player", lambda: FakePlayer())
    speaker = tts_feature.build_default_speaker(TTSConfig(engine="external"))
    assert isinstance(speaker, ExternalSpeaker)


def test_flags_build_the_external_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_TTS_TOKEN", raising=False)
    config = build_config(
        parse_args(
            [
                "--tts-engine",
                "external",
                "--tts-url",
                "http://127.0.0.1:9000",
                "--tts-external-voice",
                "narrator",
                "--tts-options",
                '{"speed": 1.1}',
            ]
        )
    )
    tts = config.tts
    assert (tts.engine, tts.external_url, tts.external_voice) == ("external", "http://127.0.0.1:9000", "narrator")
    assert tts.external_options == {"speed": 1.1} and tts.external_token == ""


def test_defaults_when_nothing_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_TTS_TOKEN", raising=False)
    tts = build_config(parse_args(["--tts-engine", "external"])).tts
    assert (tts.external_url, tts.external_voice, tts.external_options, tts.external_token) == (
        "http://127.0.0.1:8765",
        "",
        {},
        "",
    )


def test_saved_config_is_the_fallback_and_flags_and_env_win(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = PersistedConfig(
        tts_engine="external",
        tts_external_url="http://saved:1",
        tts_external_voice="saved-voice",
        tts_external_token="saved-token",
        tts_external_options='{"a": 1}',
    )
    monkeypatch.delenv("VOICEMATE_TTS_TOKEN", raising=False)
    tts = build_config(parse_args([]), saved).tts
    assert (tts.engine, tts.external_url, tts.external_voice, tts.external_token) == (
        "external",
        "http://saved:1",
        "saved-voice",
        "saved-token",
    )
    assert tts.external_options == {"a": 1}
    monkeypatch.setenv("VOICEMATE_TTS_TOKEN", "env-token")
    tts = build_config(parse_args(["--tts-url", "http://flag:2", "--tts-options", "{}"]), saved).tts
    assert (tts.external_url, tts.external_token, tts.external_options) == ("http://flag:2", "env-token", {})


def test_options_that_are_not_a_json_object_are_ignored_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    for raw in ("not json", "[1, 2]", '"text"'):
        assert build_config(parse_args(["--tts-options", raw])).tts.external_options == {}
    assert capsys.readouterr().err.count("TTS options ignored") == 3


def test_persisted_round_trip_keeps_special_characters(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    options = '{"note": "a \\"quoted\\" word", "path": "C:\\\\x"}'
    save_persisted(
        PersistedConfig(
            tts_engine="external",
            tts_external_url="http://127.0.0.1:8765",
            tts_external_voice="v",
            tts_external_token='to"ken\\with\nodd chars',
            tts_external_options=options,
        ),
        path,
    )
    loaded = load_persisted(path)
    assert loaded.tts_engine == "external"
    assert loaded.tts_external_token == 'to"ken\\with\nodd chars'
    assert loaded.tts_external_options == options


def test_a_bad_url_disables_tts_instead_of_crashing(capsys: pytest.CaptureFixture[str]) -> None:
    from app.cli.wiring import build_speaker
    from app.features.tts.base import NullSpeaker

    assert isinstance(build_speaker(TTSConfig(engine="external", external_url="not a url")), NullSpeaker)
    assert "not valid" in capsys.readouterr().err


# --- reporting ---------------------------------------------------------------------------------------------


def test_a_busy_or_rejected_request_does_not_hide_a_later_outage(capsys: pytest.CaptureFixture[str]) -> None:
    speaker = _speaker(_free_port(), FakePlayer())
    speaker._report(ProviderError("busy", "HTTP 409 busy"))
    speaker._report(ProviderError("rejected", "HTTP 400 text_too_long", code="text_too_long"))
    speaker._report(ProviderError("unreachable", "nothing is listening"))  # the first real outage is logged
    speaker._report(ProviderError("unreachable", "nothing is listening"))  # and only once
    err = capsys.readouterr().err
    assert err.count("is not reachable") == 1
    assert err.count("[ExternalSpeaker]") == 1  # the busy and rejected requests printed nothing
    assert speaker.is_active() is False  # the outage took it out of play for a while


class _NotAProvider(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits += 1
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None


def test_another_web_server_at_the_url_is_a_permanent_failure_not_a_retry_every_sentence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NotAProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        speaker = _speaker(server.server_port, FakePlayer())
        speaker.speak("Hello")
        speaker.speak("Hello again")
        assert _NotAProvider.hits == 1  # asked once, then left alone
        assert not speaker.is_active()
        assert "not a compatible TTS provider" in capsys.readouterr().err
    finally:
        server.shutdown()
        server.server_close()


def test_the_token_is_taken_out_of_the_environment_so_children_do_not_inherit_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICEMATE_TTS_TOKEN", "env-token")
    tts = build_config(parse_args(["--tts-engine", "external"])).tts
    assert tts.external_token == "env-token"
    assert "VOICEMATE_TTS_TOKEN" not in os.environ


def test_an_invalid_provider_url_is_logged_without_its_credentials(capsys: pytest.CaptureFixture[str]) -> None:
    from app.cli.wiring import build_speaker
    from app.features.tts.base import NullSpeaker

    for url in ("http://user:secretpw@host:1/", "user:secretpw@host"):
        assert isinstance(build_speaker(TTSConfig(engine="external", external_url=url)), NullSpeaker)
    err = capsys.readouterr().err
    assert "TTS disabled" in err and "secretpw" not in err and "user" not in err
