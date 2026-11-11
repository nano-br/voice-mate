"""ExternalTranscriber against the reference STT provider on a free loopback port: the provider
transcribes, the built-in engine (a fake here) takes over while it cannot, and the engine-side
configuration (flags, saved config, environment) builds it."""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from app.cli.args import parse_args
from app.cli.config_builder import build_config
from app.cli.wiring import build_transcriber, describe_transcriber
from app.core.config import Config, ExternalSttConfig
from app.features.stt_external import transcriber as external_module
from app.features.stt_external.transcriber import ExternalTranscriber, to_pcm16
from app.protocol.provider_http import ProviderError
from app.setup.persisted_config import PersistedConfig, load_persisted, save_persisted
from tools.examples.stt_provider_reference import ProviderServer, make_server

TONE: NDArray[np.float32] = (0.3 * np.sin(np.arange(32000) * 2 * np.pi * 440 / 16000)).astype(np.float32)


class FakeBuiltin:
    """The built-in engine: says who answered, counts its uses."""

    def __init__(self) -> None:
        self.calls = 0
        self.warmed = 0
        self.closed = False

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        self.calls += 1
        return "built-in text"

    def warmup(self) -> None:
        self.warmed += 1

    def close(self) -> None:
        self.closed = True


class Builder:
    def __init__(self) -> None:
        self.built: list[FakeBuiltin] = []

    def __call__(self) -> FakeBuiltin:
        engine = FakeBuiltin()
        self.built.append(engine)
        return engine


@pytest.fixture
def started() -> Iterator[list[ThreadingHTTPServer]]:
    servers: list[ThreadingHTTPServer] = []
    yield servers
    for server in servers:
        server.shutdown()
        server.server_close()


def _provider(started: list[ThreadingHTTPServer], **kwargs: object) -> ProviderServer:
    server = make_server(**kwargs)  # type: ignore[arg-type]
    started.append(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _transcriber(
    target: ThreadingHTTPServer | int, builder: Builder | None = None, *, language: str = "pt", **settings: object
) -> ExternalTranscriber:
    port = target if isinstance(target, int) else target.server_port
    config = Config(
        stt_engine="external",
        transcription_language=language,  # type: ignore[arg-type]
        stt_external=ExternalSttConfig(url=f"http://127.0.0.1:{port}", **settings),  # type: ignore[arg-type]
    )
    return ExternalTranscriber(config, builtin=builder or Builder())


# --- transcribing ----------------------------------------------------------------------------------------


def test_the_provider_transcribes_with_language_and_options(started: list[ThreadingHTTPServer]) -> None:
    server, builder = _provider(started), Builder()
    stt = _transcriber(server, builder, options={"beam_size": 5})
    assert stt.transcribe(TONE) == "I heard 2.0 seconds of audio."
    sent = server.state.requests[0]
    assert (sent["sample_rate"], sent["language"], sent["options"], sent["bytes"]) == (
        16000,
        "pt",
        {"beam_size": 5},
        64000,
    )
    assert builder.built == []  # the built-in engine is never loaded while the provider answers


def test_auto_language_sends_no_language_and_silence_is_nothing_heard(started: list[ThreadingHTTPServer]) -> None:
    server = _provider(started)
    stt = _transcriber(server, language="auto")
    assert stt.transcribe(np.zeros(16000, dtype=np.float32)) == ""
    assert server.state.requests[0]["language"] is None


def test_describe_reports_the_provider_by_name(started: list[ThreadingHTTPServer]) -> None:
    server = _provider(started)
    config = Config(
        stt_engine="external",
        stt_mode="gpu",
        stt_external=ExternalSttConfig(url=f"http://127.0.0.1:{server.server_port}"),
    )
    stt = ExternalTranscriber(config, builtin=Builder())
    assert describe_transcriber(stt, config) == {
        "mode": "gpu",
        "device": "cpu",
        "backend": "external",
        "model": "Reference duration provider",
    }


def test_warmup_connects_and_calls_warmup(
    started: list[ThreadingHTTPServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server = _provider(started, languages=["pt", "en"])
    _transcriber(server).warmup()
    assert server.state.warmups == 1
    assert "Connected to 'Reference duration provider' (pt, en)" in capsys.readouterr().out


def test_a_loading_provider_is_waited_for(started: list[ThreadingHTTPServer], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(external_module, "LOADING_POLL_SECONDS", 0.05)
    server = _provider(started, loading_seconds=0.4)
    assert _transcriber(server).transcribe(TONE).startswith("I heard")


def test_audio_is_resampled_when_the_provider_takes_another_rate() -> None:
    pcm = to_pcm16(TONE, 16000, 8000)
    assert len(pcm) == 16000 * 2  # 2 s at 8 kHz
    assert (
        to_pcm16(np.array([2.0, -2.0, 0.5], dtype=np.float32), 16000, 16000)
        == np.array([32767, -32767, 16383], dtype="<i2").tobytes()
    )


# --- the fallback ----------------------------------------------------------------------------------------


def test_a_provider_that_is_down_falls_back_logs_once_and_is_retried_later(
    started: list[ThreadingHTTPServer], capsys: pytest.CaptureFixture[str]
) -> None:
    port, builder = _free_port(), Builder()
    stt = _transcriber(port, builder)
    assert stt.model_name == "external provider"
    assert stt.transcribe(TONE) == "built-in text"
    assert stt.transcribe(TONE) == "built-in text"
    assert len(builder.built) == 1 and builder.built[0].calls == 2 and builder.built[0].warmed == 1
    err = capsys.readouterr().err
    assert err.count("is not reachable") == 1 and f"127.0.0.1:{port}" in err
    assert "tried again in a few seconds" in err
    server = make_server("127.0.0.1", port)
    started.append(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    assert stt.transcribe(TONE) == "built-in text"  # still inside the pause: not asked yet
    assert server.state.requests == []
    stt._down_until = 0.0  # the pause has passed
    assert stt.transcribe(TONE).startswith("I heard")
    assert len(builder.built) == 1  # the built-in engine is kept for the next outage


def test_an_outage_after_a_recovery_is_logged_again(capsys: pytest.CaptureFixture[str]) -> None:
    stt = _transcriber(_free_port())
    stt._report(ProviderError("unreachable", "nothing is listening"))
    stt._report(ProviderError("server", "HTTP 500"))
    assert capsys.readouterr().err.count("⚠") == 1
    stt._outage_logged = False  # what a successful connection does
    stt._report(ProviderError("not_ready", "HTTP 503"))
    assert "not ready yet" in capsys.readouterr().err


def test_a_busy_or_refused_recording_falls_back_for_that_recording_only(
    started: list[ThreadingHTTPServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server, builder = _provider(started), Builder()
    stt = _transcriber(server, builder)
    server.state.busy = True
    assert stt.transcribe(TONE) == "built-in text"
    server.state.busy = False
    assert stt.transcribe(TONE).startswith("I heard")  # no pause after a busy answer
    err = capsys.readouterr().err
    assert "is busy" in err and "for this recording" in err


def test_a_recording_longer_than_max_seconds_is_not_sent(
    started: list[ThreadingHTTPServer], monkeypatch: pytest.MonkeyPatch
) -> None:
    server, builder = _provider(started), Builder()
    stt = _transcriber(server, builder)
    stt.transcribe(TONE)
    assert stt._info is not None
    monkeypatch.setattr(stt, "_info", stt._info.__class__(**{**stt._info.__dict__, "max_seconds": 1.0}))
    assert stt.transcribe(TONE) == "built-in text"
    assert len(server.state.requests) == 1
    assert stt.transcribe(TONE[:8000]).startswith("I heard")  # the next short one goes to the provider


def test_a_wrong_token_is_permanent_and_never_printed(
    started: list[ThreadingHTTPServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server, builder = _provider(started, token="the-real-token"), Builder()
    stt = _transcriber(server, builder, token="a-wrong-token")
    assert stt.transcribe(TONE) == "built-in text"
    assert stt.transcribe(TONE) == "built-in text"
    captured = capsys.readouterr()
    assert captured.err.count("rejected the token") == 1 and "until the settings change" in captured.err
    assert "the-real-token" not in captured.out + captured.err and "a-wrong-token" not in captured.out + captured.err
    assert "a-wrong-token" not in repr(ExternalSttConfig(token="a-wrong-token"))
    assert _transcriber(server, token="the-real-token").transcribe(TONE).startswith("I heard")


def test_a_language_the_provider_does_not_list_is_permanent(
    started: list[ThreadingHTTPServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server, builder = _provider(started, languages=["en"]), Builder()
    stt = _transcriber(server, builder, language="pt")
    assert stt.transcribe(TONE) == "built-in text"
    assert stt.transcribe(TONE) == "built-in text"
    assert server.state.requests == []
    assert capsys.readouterr().err.count("does not list the language") == 1


class _NotAProvider(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits += 1
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return None


def test_another_web_server_at_the_url_is_permanent_not_a_retry_every_recording(
    started: list[ThreadingHTTPServer], capsys: pytest.CaptureFixture[str]
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NotAProvider)
    started.append(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stt = _transcriber(server)
    hits = _NotAProvider.hits  # the startup probe asked once
    stt.transcribe(TONE)
    stt.transcribe(TONE)
    assert _NotAProvider.hits == hits + 1
    assert "not a compatible STT provider" in capsys.readouterr().err


def test_without_the_fallback_the_error_surfaces_and_the_provider_is_asked_every_time(
    started: list[ThreadingHTTPServer], capsys: pytest.CaptureFixture[str]
) -> None:
    port, builder = _free_port(), Builder()
    stt = _transcriber(port, builder, fallback=False)
    with pytest.raises(RuntimeError, match="not reachable"):
        stt.transcribe(TONE)
    server = make_server("127.0.0.1", port)
    started.append(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    assert stt.transcribe(TONE).startswith("I heard")  # no pause without a fallback to bridge it
    assert builder.built == []
    assert "built-in" not in capsys.readouterr().err


def test_without_the_fallback_a_permanent_problem_keeps_failing(started: list[ThreadingHTTPServer]) -> None:
    server, builder = _provider(started, token="t"), Builder()
    stt = _transcriber(server, builder, fallback=False)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="rejected the token"):
            stt.transcribe(TONE)
    assert builder.built == []


def test_close_closes_the_built_in_engine() -> None:
    builder = Builder()
    stt = _transcriber(_free_port(), builder)
    stt.transcribe(TONE)
    stt.close()
    assert builder.built[0].closed


# --- configuration ---------------------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEMATE_STT_TOKEN", raising=False)
    monkeypatch.delenv("VOICEMATE_STT_START_COMMAND", raising=False)


@pytest.mark.usefixtures("clean_env")
def test_defaults_are_the_built_in_engine() -> None:
    config = build_config(parse_args([]))
    assert config.stt_engine == "builtin"
    assert config.stt_external == ExternalSttConfig()
    assert config.stt_external.url == "http://127.0.0.1:8766" and config.stt_external.fallback


@pytest.mark.usefixtures("clean_env")
def test_flags_build_the_external_config() -> None:
    config = build_config(
        parse_args(
            [
                "--stt-engine",
                "external",
                "--stt-url",
                "http://127.0.0.1:9000/stt",
                "--stt-options",
                '{"beam_size": 5}',
                "--no-stt-fallback",
            ]
        )
    )
    external = config.stt_external
    assert config.stt_engine == "external"
    assert (external.url, external.options, external.fallback, external.token) == (
        "http://127.0.0.1:9000/stt",
        {"beam_size": 5},
        False,
        "",
    )


def test_saved_config_is_the_fallback_and_flags_and_env_win(monkeypatch: pytest.MonkeyPatch) -> None:
    saved = PersistedConfig(
        stt_engine="external",
        stt_external_url="http://saved:1",
        stt_external_token="saved-token",
        stt_external_options='{"a": 1}',
        stt_fallback=False,
    )
    monkeypatch.delenv("VOICEMATE_STT_TOKEN", raising=False)
    config = build_config(parse_args([]), saved)
    external = config.stt_external
    assert (config.stt_engine, external.url, external.token, external.options, external.fallback) == (
        "external",
        "http://saved:1",
        "saved-token",
        {"a": 1},
        False,
    )
    monkeypatch.setenv("VOICEMATE_STT_TOKEN", "env-token")
    monkeypatch.setenv("VOICEMATE_STT_START_COMMAND", "  start-it  ")
    config = build_config(
        parse_args(["--stt-engine", "builtin", "--stt-url", "http://flag:2", "--stt-options", "{}"]), saved
    )
    external = config.stt_external
    assert config.stt_engine == "builtin"
    assert (external.url, external.token, external.options, external.start_command) == (
        "http://flag:2",
        "env-token",
        {},
        "start-it",
    )
    # The secrets are taken out of the environment: the processes the engine starts do not inherit them.
    assert "VOICEMATE_STT_TOKEN" not in os.environ and "VOICEMATE_STT_START_COMMAND" not in os.environ


@pytest.mark.usefixtures("clean_env")
def test_options_that_are_not_a_json_object_are_ignored_with_a_warning(capsys: pytest.CaptureFixture[str]) -> None:
    for raw in ("not json", "[1, 2]", '"text"'):
        assert build_config(parse_args(["--stt-options", raw])).stt_external.options == {}
    assert capsys.readouterr().err.count("STT options ignored") == 3


def test_persisted_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    options = '{"prompt": "a \\"quoted\\" word"}'
    save_persisted(
        PersistedConfig(
            stt_engine="external",
            stt_external_url="http://127.0.0.1:8766/stt",
            stt_external_token='to"ken\\odd',
            stt_external_options=options,
            stt_fallback=False,
        ),
        path,
    )
    loaded = load_persisted(path)
    assert (loaded.stt_engine, loaded.stt_external_url, loaded.stt_fallback) == (
        "external",
        "http://127.0.0.1:8766/stt",
        False,
    )
    assert loaded.stt_external_token == 'to"ken\\odd' and loaded.stt_external_options == options
    path.write_text('stt_engine = "cloud"\nstt_fallback = "no"\n', encoding="utf-8")
    assert load_persisted(path).stt_engine is None and load_persisted(path).stt_fallback is None


def test_build_transcriber_builds_the_external_one_first(
    started: list[ThreadingHTTPServer], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.cli import wiring

    builder = Builder()
    monkeypatch.setattr(wiring, "build_builtin_transcriber", lambda _config: builder())
    server = _provider(started)
    config = Config(
        stt_engine="external", stt_external=ExternalSttConfig(url=f"http://127.0.0.1:{server.server_port}/")
    )
    stt = build_transcriber(config)
    assert isinstance(stt, ExternalTranscriber) and builder.built == []
    stt._down_until = 1e18  # the provider is out of play: the built-in engine comes from the normal chain
    stt._outage_logged = True
    assert stt.transcribe(TONE) == "built-in text" and len(builder.built) == 1


def test_an_invalid_url_uses_the_built_in_engine_without_logging_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import wiring

    builtin = FakeBuiltin()
    monkeypatch.setattr(wiring, "build_builtin_transcriber", lambda _config: builtin)
    for url in ("http://user:secretpw@host:1/", "not a url"):
        config = Config(stt_engine="external", stt_external=ExternalSttConfig(url=url))
        assert build_transcriber(config) is builtin
    err = capsys.readouterr().err
    assert err.count("is not valid") == 2 and "secretpw" not in err and "user" not in err
