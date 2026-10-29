"""The assistant's voice (`EngineVoice`) against the reference provider, which speaks the same
protocol as the engine's `/tts` endpoint, with a fake audio output."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
from companion_core_fakes import use_english, wait_until

from app.companion.voice import (
    LOADING_RETRY_S,
    RETRY_AFTER_S,
    EngineVoice,
    complete_sentences,
    engine_tts_url,
    explain,
)
from app.protocol.tts_provider import ProviderClient, ProviderError, SpeechStream
from tools.examples.tts_provider_reference import ProviderServer, make_server


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    use_english(monkeypatch)


class FakeOutput:
    """A `PcmOutput` that keeps what it was given; nothing is played."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.opened: list[tuple[int, int]] = []
        self.data = bytearray()
        self.writes = 0
        self.commits = 0
        self.flushes = 0
        self.closes = 0

    def open(self, sample_rate: int, channels: int) -> None:
        self.opened.append((sample_rate, channels))

    def write(self, data: bytes) -> None:
        with self.lock:
            self.data += data
            self.writes += 1

    def commit(self) -> None:
        self.commits += 1

    def pending(self) -> bool:
        return False

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closes += 1


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _serve(**options: object) -> ProviderServer:
    server = make_server(**options)  # type: ignore[arg-type]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def provider() -> Iterator[ProviderServer]:
    server = _serve(pace=0.0)
    yield server
    server.shutdown()
    server.server_close()


def _client(server: ProviderServer) -> ProviderClient:
    return ProviderClient(f"http://127.0.0.1:{server.server_port}")


def _texts(server: ProviderServer) -> list[str]:
    with server.state.lock:
        return [str(body["text"]) for body in server.state.requests]


class Rig:
    def __init__(self, server: ProviderServer | None, *, voice: str = "", output: bool = True) -> None:
        self.server = server
        self.output = FakeOutput()
        self.clock = Clock()
        self.voice_name = voice
        self.fallback: list[str] = []
        self.reasons: list[str] = []
        self.voice = EngineVoice(
            lambda: _client(self.server) if self.server is not None else None,
            (lambda: self.output) if output else (lambda: None),
            voice=lambda: self.voice_name,
            fallback=lambda text, _language: self.fallback.append(text),
            clock=self.clock,
        )
        self.voice.on_unavailable(self.reasons.append)


@pytest.fixture
def rigs() -> Iterator[list[Rig]]:
    made: list[Rig] = []
    yield made
    for rig in made:
        rig.voice.close()


def _rig(rigs: list[Rig], server: ProviderServer | None, **options: object) -> Rig:
    rig = Rig(server, **options)  # type: ignore[arg-type]
    rigs.append(rig)
    return rig


# --- sentences ---------------------------------------------------------------------------------------------


def test_complete_sentences_waits_for_the_end_of_a_sentence() -> None:
    assert complete_sentences("Hello there. How are", False) == (["Hello there."], 12)
    assert complete_sentences("Hello there. How are", True) == (["Hello there.", "How are"], 20)
    assert complete_sentences("Is it 3.5 now? Yes!", True) == (["Is it 3.5 now?", "Yes!"], 19)
    assert complete_sentences("No end yet", False) == ([], 0)


def test_the_engine_url_is_the_daemon_with_the_tts_prefix() -> None:
    assert engine_tts_url("127.0.0.1", 47821) == "http://127.0.0.1:47821/tts"


# --- speaking ----------------------------------------------------------------------------------------------


def test_a_reply_is_spoken_sentence_by_sentence_in_order(provider: ProviderServer, rigs: list[Rig]) -> None:
    rig = _rig(rigs, provider)
    rig.voice.speak("r1", "Hello there. How are", final=False)
    rig.voice.speak("r1", "Hello there. How are you? Fine.", final=False)
    rig.voice.speak("r1", "Hello there. How are you? Fine. Bye", final=True)
    assert wait_until(lambda: len(_texts(provider)) == 4)
    assert _texts(provider) == ["Hello there.", "How are you?", "Fine.", "Bye"]
    assert wait_until(lambda: rig.output.commits == 4)
    assert rig.output.opened[0] == (24000, 1)
    assert len(rig.output.data) > 0 and len(rig.output.data) % 2 == 0  # whole 16-bit frames
    assert all(body["format"] == "pcm_s16le" for body in provider.state.requests)
    assert rig.voice.reason == ""


def test_expression_tags_are_dropped_unless_the_engine_advertises_them(rigs: list[Rig]) -> None:
    plain, tagged = _serve(pace=0.0), _serve(pace=0.0, expression_tags=True)
    try:
        for server in (plain, tagged):
            _rig(rigs, server).voice.speak("r", "[laugh] That was fun.", final=True)
        assert wait_until(lambda: bool(_texts(plain)) and bool(_texts(tagged)))
        assert "[laugh]" not in _texts(plain)[0] and "That was fun." in _texts(plain)[0]
        assert _texts(tagged) == ["[laugh] That was fun."]
    finally:
        for server in (plain, tagged):
            server.shutdown()
            server.server_close()


def test_the_configured_voice_is_asked_for_and_the_test_overrides_it(provider: ProviderServer, rigs: list[Rig]) -> None:
    rig = _rig(rigs, provider, voice="high")
    rig.voice.notice("An answer is ready.", "en")
    assert wait_until(lambda: len(_texts(provider)) == 1)
    rig.voice.test("Testing.", "low")
    assert wait_until(lambda: len(_texts(provider)) == 2)
    assert [body["voice"] for body in provider.state.requests] == ["high", "low"]
    rig.voice.test("Testing.", "")
    assert wait_until(lambda: len(_texts(provider)) == 3)
    assert provider.state.requests[-1].get("voice") is None  # "" = the engine's default voice


# --- stopping ----------------------------------------------------------------------------------------------


def test_stop_silences_at_once_and_drops_what_was_queued(rigs: list[Rig]) -> None:
    server = _serve(pace=1.0)  # real time: a long sentence plays for seconds
    try:
        rig = _rig(rigs, server)
        long_line = " ".join(["speaking"] * 40) + "."
        rig.voice.speak("r", f"{long_line} Second sentence. Third one.", final=True)
        assert wait_until(lambda: rig.output.writes > 0)
        rig.voice.stop()
        assert rig.output.flushes == 1
        assert wait_until(lambda: not server.state.active)  # the provider stopped producing
        written = len(rig.output.data)
        threading.Event().wait(0.3)
        assert _texts(server) == [long_line]  # the queued sentences were dropped
        assert len(rig.output.data) == written  # nothing played after the stop
        rig.voice.speak("r2", "After the stop.", final=True)  # it speaks again afterwards
        assert wait_until(lambda: len(_texts(server)) == 2)
    finally:
        server.shutdown()
        server.server_close()


def test_close_ends_the_worker_and_ignores_later_lines(provider: ProviderServer, rigs: list[Rig]) -> None:
    rig = _rig(rigs, provider)
    rig.voice.notice("One.", "en")
    assert wait_until(lambda: len(_texts(provider)) == 1)
    rig.voice.close()
    rig.voice.notice("Two.", "en")
    threading.Event().wait(0.1)
    assert _texts(provider) == ["One."]


def test_stopping_while_the_engine_still_sends_headers_cancels_that_request(rigs: list[Rig]) -> None:
    server = _serve(pace=0.0)
    asked, release = threading.Event(), threading.Event()
    cancelled: list[str] = []
    ids: list[str] = []

    class Slow(ProviderClient):
        def speak(self, text: str, **kwargs: object) -> SpeechStream:  # type: ignore[override]
            ids.append(str(kwargs["request_id"]))
            asked.set()
            release.wait(5)
            return super().speak(text, **kwargs)  # type: ignore[arg-type]

        def cancel(self, request_id: str) -> None:
            cancelled.append(request_id)

    try:
        rig = _rig(rigs, server)
        rig.voice._client_factory = lambda: Slow(f"http://127.0.0.1:{server.server_port}")  # type: ignore[assignment]
        rig.voice.notice("Hello there.", "en")
        assert asked.wait(5)
        rig.voice.stop()
        assert wait_until(lambda: cancelled == ids)  # the engine hears the cancel before the headers come
        release.set()
        assert wait_until(lambda: not server.state.active)  # and the late stream is dropped, not played
        assert rig.output.data == b""
    finally:
        release.set()
        server.shutdown()
        server.server_close()


def test_a_busy_engine_right_after_a_stop_is_asked_again_instead_of_dropping_the_line(
    provider: ProviderServer, rigs: list[Rig]
) -> None:
    rig = _rig(rigs, provider)
    with provider.state.lock:
        provider.state.active["still-winding-down"] = threading.Event()  # the previous stream, not gone yet
    rig.voice.notice("First words after the stop.", "en")
    time.sleep(0.3)
    with provider.state.lock:
        provider.state.active.clear()
    assert wait_until(lambda: _texts(provider) == ["First words after the stop."])
    assert rig.fallback == []


def test_audio_in_another_format_than_asked_is_refused(provider: ProviderServer, rigs: list[Rig]) -> None:
    class Wrong(ProviderClient):
        def speak(self, text: str, **kwargs: object) -> SpeechStream:  # type: ignore[override]
            stream = super().speak(text, **kwargs)  # type: ignore[arg-type]
            stream.audio_format = "pcm_f32le"  # as if the engine had answered float samples
            return stream

    rig = _rig(rigs, provider)
    rig.voice._client_factory = lambda: Wrong(f"http://127.0.0.1:{provider.server_port}")  # type: ignore[assignment]
    rig.voice.notice("Hello.", "en")
    assert wait_until(lambda: bool(rig.reasons))
    assert rig.output.data == b""  # no noise: float samples are never written as 16-bit
    assert rig.fallback == ["Hello."]


def test_an_engine_still_loading_its_voice_is_asked_to_load_and_tried_again_soon(rigs: list[Rig]) -> None:
    server = _serve(pace=0.0, loading_seconds=60.0)
    try:
        rig = _rig(rigs, server)
        rig.voice.notice("First.", "en")
        assert wait_until(lambda: rig.fallback == ["First."])
        assert rig.reasons == ["the engine is still loading"]
        assert wait_until(lambda: server.state.warmups == 1)  # on-demand load, no warmup was done at start
        assert _texts(server) == []
        server.state.loading_until = 0.0
        rig.clock.now += LOADING_RETRY_S + 1  # much sooner than the usual retry window
        rig.voice.notice("Second.", "en")
        assert wait_until(lambda: _texts(server) == ["Second."])
        assert wait_until(lambda: rig.voice.reason == "")
    finally:
        server.shutdown()
        server.server_close()


# --- when the engine cannot speak --------------------------------------------------------------------------


def test_without_an_engine_notices_fall_back_and_the_reason_is_reported_once(rigs: list[Rig]) -> None:
    rig = _rig(rigs, None)
    rig.voice.speak("r", "A reply.", final=True)
    rig.voice.notice("An answer is ready.", "en")
    rig.voice.notice("Another one.", "en")
    assert wait_until(lambda: len(rig.fallback) == 2)
    assert rig.fallback == ["An answer is ready.", "Another one."]  # the reply stays text only
    assert rig.reasons == ["the engine is not running"]
    assert rig.voice.reason == "the engine is not running"


def test_the_engine_is_tried_again_after_the_retry_window(provider: ProviderServer, rigs: list[Rig]) -> None:
    rig = _rig(rigs, None)
    rig.voice.notice("First.", "en")
    assert wait_until(lambda: rig.fallback == ["First."])
    rig.server = provider  # the engine came up, but the window has not passed
    rig.voice.notice("Second.", "en")
    assert wait_until(lambda: rig.fallback == ["First.", "Second."])
    assert _texts(provider) == []
    rig.clock.now += RETRY_AFTER_S + 1
    rig.voice.notice("Third.", "en")
    assert wait_until(lambda: _texts(provider) == ["Third."])
    assert wait_until(lambda: rig.voice.reason == "")
    assert rig.reasons == ["the engine is not running"]


def test_an_unknown_voice_is_reported_with_where_to_fix_it(provider: ProviderServer, rigs: list[Rig]) -> None:
    rig = _rig(rigs, provider, voice="nobody")
    rig.voice.notice("Hello.", "en")
    assert wait_until(lambda: bool(rig.reasons))
    assert rig.reasons == ["the engine has no voice 'nobody'. Pick one in Settings, Assistant"]
    assert rig.fallback == ["Hello."]


def test_no_audio_output_is_reported(provider: ProviderServer, rigs: list[Rig]) -> None:
    rig = _rig(rigs, provider, output=False)
    rig.voice.notice("Hello.", "en")
    assert wait_until(lambda: bool(rig.reasons))
    assert rig.reasons == ["no audio output is available on this computer"]


def test_voices_reads_the_engine_info(provider: ProviderServer, rigs: list[Rig]) -> None:
    info = _rig(rigs, provider).voice.voices()
    assert [voice.id for voice in info.voices] == ["low", "mid", "high"]
    with pytest.raises(ProviderError):
        _rig(rigs, None).voice.voices()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ProviderError("unreachable", "x"), "the engine is not running"),
        (ProviderError("unauthorized", "x"), "the engine refused the access token"),
        (ProviderError("protocol", "x"), "the engine is too old for the assistant voice"),
        (
            ProviderError("not_ready", "engine not supported for the assistant voice"),
            "the voice engine set up in the engine cannot speak for the assistant",
        ),
        (ProviderError("not_ready", "text to speech is off in the engine"), "text to speech is off in the engine"),
        (ProviderError("not_ready", "the engine is loading"), "the engine is still loading"),
    ],
)
def test_explain_names_the_reason(error: ProviderError, reason: str) -> None:
    assert explain(error) == reason
