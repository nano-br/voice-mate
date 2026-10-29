"""The engine's `/tts` voice (protocol voicemate-tts v1) over real HTTP, with a fake model.

The shared protocol client (`app.protocol.tts_provider`, what the companion uses) talks to an
`ApiServer` whose endpoint is backed by a fake `PcmSynthesizer`, a Kokoro speaker with a fake
pipeline, or the external speaker relaying the reference provider started in-process.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import numpy as np
import pytest
from numpy.typing import NDArray

from app.core.config import TTSConfig
from app.core.session_status import CancelOutcome, SessionStatus, ToggleOutcome
from app.daemon.server import ApiServer, EngineInfo
from app.daemon.tts_endpoint import UNSUPPORTED, TtsEndpoint
from app.features.tts.base import NullSpeaker
from app.features.tts.external_speaker import ExternalSpeaker
from app.features.tts.kokoro_speaker import KokoroSpeaker, kokoro_voices
from app.features.tts.pcm import GatedSpeaker, PcmInfo, PcmVoice, SpeechGate, SynthesisError, to_pcm_bytes
from app.protocol.models import TriggerExpect
from app.protocol.tts_provider import FORMAT_F32, ProviderClient, ProviderError
from tools.examples.tts_provider_reference import make_server

TOKEN = "s3cret"
INFO = EngineInfo(platform="wsl2", trigger="socket")


class FakeControl:
    def toggle(self, handler_id: str, client_id: str | None = None, expect: TriggerExpect = "toggle") -> ToggleOutcome:
        return ToggleOutcome(action="started", op_seq=1, state="recording", flow=handler_id)

    def cancel(self, op_seq: int | None = None) -> CancelOutcome:
        return CancelOutcome(action="cancelled", state="idle")


class FakeStream:
    sample_rate = 16000

    def __init__(self, chunks: list[NDArray[np.float32]], pause: float) -> None:
        self._chunks = chunks
        self._pause = pause
        self.closed = threading.Event()

    def chunks(self) -> Iterator[NDArray[np.float32]]:
        for chunk in self._chunks:
            if self.closed.is_set():
                return
            yield chunk
            if self._pause and self.closed.wait(self._pause):
                return

    def close(self) -> None:
        self.closed.set()


class FakeSynth:
    """A PcmSynthesizer with a fake model: `chunks` per request, `pause` seconds between them."""

    def __init__(self, chunks: int = 3, pause: float = 0.0, voices: tuple[PcmVoice, ...] = ()) -> None:
        self.voices = voices or (PcmVoice("alto", "Alto", ("en",), True), PcmVoice("bass", "Bass"))
        self.count = chunks
        self.pause = pause
        self.opened: list[tuple[str, str | None]] = []
        self.streams: list[FakeStream] = []
        self.ready = True
        self.warmups = 0

    def is_active(self) -> bool:
        return True

    def speak(self, text: str) -> None: ...

    def warmup(self) -> None: ...

    def wait_done(self, timeout: float | None = None) -> bool:
        return True

    def stop(self) -> None: ...

    def close(self) -> None: ...

    def pcm_info(self) -> PcmInfo:
        return PcmInfo("Fake voice", 16000, self.voices, ("streaming",))

    def pcm_ready(self) -> bool:
        return self.ready

    def pcm_warmup(self) -> None:
        self.warmups += 1

    def open_pcm(self, text: str, *, voice: str | None) -> FakeStream:
        if voice is not None and voice not in {v.id for v in self.voices}:
            raise SynthesisError("unknown_voice", f"unknown voice {voice!r}")
        self.opened.append((text, voice))
        stream = FakeStream([np.full(1600, 0.5, dtype=np.float32) for _ in range(self.count)], self.pause)
        self.streams.append(stream)
        return stream


class NoPcmSpeaker:
    """An engine whose speaker can only play (no PCM synthesis)."""

    def is_active(self) -> bool:
        return True

    def speak(self, text: str) -> None: ...

    def warmup(self) -> None: ...

    def wait_done(self, timeout: float | None = None) -> bool:
        return True

    def stop(self) -> None: ...

    def close(self) -> None: ...


@pytest.fixture
def serve() -> Iterator[Callable[..., ApiServer]]:
    servers: list[ApiServer] = []

    def make(speaker: object | None = None, *, token: str | None = None, attach: bool = True) -> ApiServer:
        server = ApiServer(SessionStatus(), info=INFO, control=FakeControl(), port=0, token=token)
        if attach:
            server.tts.attach(speaker)  # type: ignore[arg-type]
        server.start()
        servers.append(server)
        return server

    yield make
    for server in servers:
        server.stop()


def client(server: ApiServer, token: str | None = None) -> ProviderClient:
    return ProviderClient(f"http://127.0.0.1:{server.port}/tts", token)


def test_info_lists_the_engine_voices(serve: Callable[..., ApiServer]) -> None:
    info = client(serve(FakeSynth())).info()
    assert info.name == "Fake voice"
    assert [v.id for v in info.voices] == ["alto", "bass"]
    assert info.voices[0].default
    assert info.sample_rate == 16000
    assert info.formats == ("pcm_s16le", "pcm_f32le")
    assert info.supports("streaming") and not info.supports("expression_tags")
    assert info.max_chars == 2000


def test_a_synthesizer_without_voices_offers_one_default(serve: Callable[..., ApiServer]) -> None:
    synth = FakeSynth()
    synth.voices = ()
    server = serve(synth)
    info = client(server).info()
    assert [(v.id, v.default) for v in info.voices] == [("default", True)]
    stream = client(server).speak("Hi.", voice="default")
    assert b"".join(stream.chunks())
    assert synth.opened == [("Hi.", None)]


def test_speak_streams_s16_pcm(serve: Callable[..., ApiServer]) -> None:
    synth = FakeSynth(chunks=3)
    stream = client(serve(synth)).speak("Hello there.", voice="bass")
    assert (stream.sample_rate, stream.channels, stream.audio_format) == (16000, 1, "pcm_s16le")
    audio = b"".join(stream.chunks())
    assert len(audio) == 3 * 1600 * 2
    assert np.frombuffer(audio, dtype="<i2")[0] == int(0.5 * 32767)
    assert synth.opened == [("Hello there.", "bass")]


def test_speak_streams_f32_on_request(serve: Callable[..., ApiServer]) -> None:
    stream = client(serve(FakeSynth(chunks=1))).speak("Hi.", audio_format=FORMAT_F32)
    audio = b"".join(stream.chunks())
    assert stream.audio_format == FORMAT_F32
    assert np.frombuffer(audio, dtype="<f4")[0] == pytest.approx(0.5)


def test_unknown_voice_is_a_400(serve: Callable[..., ApiServer]) -> None:
    with pytest.raises(ProviderError) as caught:
        client(serve(FakeSynth())).speak("Hi.", voice="soprano")
    assert (caught.value.kind, caught.value.code) == ("rejected", "unknown_voice")


def test_bad_requests_follow_the_protocol(serve: Callable[..., ApiServer]) -> None:
    server = serve(FakeSynth())
    with pytest.raises(ProviderError) as caught:
        client(server).speak("Hi.", audio_format="mp3")
    assert caught.value.code == "unsupported_format"
    with pytest.raises(ProviderError) as caught:
        client(server).speak("x" * 2001)
    assert caught.value.code == "text_too_long"


def test_health_and_warmup(serve: Callable[..., ApiServer]) -> None:
    synth = FakeSynth()
    synth.ready = False
    server = serve(synth)
    assert client(server).health() == "loading"
    synth.ready = True
    assert client(server).health() == "ok"
    client(server).warmup()
    assert synth.warmups == 1


def test_before_the_engine_is_built_it_is_loading(serve: Callable[..., ApiServer]) -> None:
    server = serve(attach=False)
    assert client(server).health() == "loading"
    with pytest.raises(ProviderError) as caught:
        client(server).info()
    assert caught.value.kind == "not_ready"


@pytest.mark.parametrize(
    ("speaker", "reason"),
    [(NoPcmSpeaker(), UNSUPPORTED), (NullSpeaker(), "text to speech is off"), (None, "text to speech is off")],
)
def test_engines_without_pcm_answer_not_ready(serve: Callable[..., ApiServer], speaker: object, reason: str) -> None:
    server = serve(GatedSpeaker(speaker, SpeechGate()) if speaker is not None else None)  # type: ignore[arg-type]
    for call in (client(server).info, client(server).health, lambda: client(server).speak("Hi.")):
        with pytest.raises(ProviderError) as caught:
            call()
        assert caught.value.kind == "not_ready"
        assert reason in caught.value.detail


def test_the_daemon_token_protects_the_voice(serve: Callable[..., ApiServer]) -> None:
    server = serve(FakeSynth(), token=TOKEN)
    with pytest.raises(ProviderError) as caught:
        client(server).info()
    assert caught.value.kind == "unauthorized"
    assert client(server, TOKEN).info().name == "Fake voice"


def test_one_stream_at_a_time(serve: Callable[..., ApiServer]) -> None:
    server = serve(FakeSynth(chunks=50, pause=0.05))
    first = client(server).speak("A long sentence.")
    try:
        with pytest.raises(ProviderError) as caught:
            client(server).speak("Another one.")
        assert caught.value.kind == "busy"
    finally:
        first.close()


def test_busy_while_the_engine_flow_speaks(serve: Callable[..., ApiServer]) -> None:
    server = serve(FakeSynth())
    server.tts.gate.begin_local()
    with pytest.raises(ProviderError) as caught:
        client(server).speak("Hi.")
    assert caught.value.kind == "busy"
    server.tts.gate.end_local()
    assert b"".join(client(server).speak("Hi.").chunks())


def test_the_engine_flow_interrupts_a_stream(serve: Callable[..., ApiServer]) -> None:
    synth = FakeSynth(chunks=200, pause=0.02)
    server = serve(synth)
    stream = client(server).speak("A long answer.")
    next(stream.chunks())
    started = time.monotonic()
    server.tts.gate.begin_local()
    assert time.monotonic() - started < 1.0
    assert synth.streams[0].closed.is_set()
    assert len(b"".join(stream.chunks())) < 200 * 1600 * 2
    assert server.tts.gate.owner == "local"


def test_the_engine_flow_wins_over_a_stream_that_is_still_opening() -> None:
    opening, release = threading.Event(), threading.Event()

    class SlowOpen(FakeSynth):
        def open_pcm(self, text: str, *, voice: str | None) -> FakeStream:
            opening.set()
            release.wait(5)
            return super().open_pcm(text, voice=voice)

    synth = SlowOpen()
    endpoint = TtsEndpoint()
    endpoint.attach(synth)
    replies: list[object] = []
    speak = threading.Thread(
        target=lambda: replies.append(endpoint.handle("POST", "/tts/v1/speak", {"request_id": "r1", "text": "Hi."}))
    )
    speak.start()
    assert opening.wait(2)
    flow = threading.Thread(target=endpoint.gate.begin_local)
    flow.start()
    time.sleep(0.1)  # begin_local found a remote owner with no stream to close yet
    release.set()
    speak.join(3)
    flow.join(3)
    assert not speak.is_alive() and not flow.is_alive()
    reply = replies[0]
    assert getattr(reply, "status", None) == 409 and getattr(reply, "stream", None) is None
    assert synth.streams[0].closed.is_set()  # the late stream never plays next to the flow
    assert endpoint.gate.owner == "local"


def test_cancel_and_closing_the_connection_stop_the_synthesis(serve: Callable[..., ApiServer]) -> None:
    synth = FakeSynth(chunks=200, pause=0.02)
    server = serve(synth)
    stream = client(server).speak("A long answer.")
    next(stream.chunks())
    client(server).cancel(stream.request_id)
    assert synth.streams[0].closed.wait(1.0)
    stream.close()
    assert _wait_until(lambda: server.tts.gate.owner is None)
    second = client(server).speak("Again.")
    next(second.chunks())
    second.close()  # only the connection, no cancel
    assert synth.streams[1].closed.wait(2.0)
    assert _wait_until(lambda: server.tts.gate.owner is None)


def test_stopping_the_server_ends_a_stream(serve: Callable[..., ApiServer]) -> None:
    synth = FakeSynth(chunks=500, pause=0.02)
    server = serve(synth)
    stream = client(server).speak("A long answer.")
    next(stream.chunks())
    server.stop()
    assert synth.streams[0].closed.is_set()


def test_browser_requests_are_refused(serve: Callable[..., ApiServer]) -> None:
    import urllib.error
    import urllib.request

    server = serve(FakeSynth())
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/tts/v1/info", headers={"Origin": "https://example.com"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.HTTPError) as caught:
        opener.open(request, timeout=5)
    assert caught.value.code == 403


def _wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- the speech gate and the engine's own speaker ------------------------------------------------


def test_gated_speaker_holds_the_gate_for_the_turn() -> None:
    gate = SpeechGate()
    speaker = GatedSpeaker(FakeSynth(), gate)
    speaker.speak("One.")
    speaker.speak("Two.")
    assert gate.owner == "local"
    assert not gate.begin_remote()
    speaker.wait_done()
    assert gate.owner is None
    speaker.speak("Three.")
    speaker.stop()
    assert gate.owner is None
    speaker.speak("Four.")
    speaker.end_turn()
    assert gate.begin_remote()


def test_to_pcm_bytes_clips_and_converts() -> None:
    samples = np.array([0.0, 1.0, -1.0, 2.0], dtype=np.float32)
    assert np.frombuffer(to_pcm_bytes(samples, "pcm_s16le"), dtype="<i2").tolist() == [0, 32767, -32767, 32767]
    assert np.frombuffer(to_pcm_bytes(samples, "pcm_f32le"), dtype="<f4").tolist() == [0.0, 1.0, -1.0, 2.0]


def test_endpoint_attach_sees_through_the_gated_speaker() -> None:
    endpoint = TtsEndpoint()
    endpoint.attach(GatedSpeaker(FakeSynth(), endpoint.gate))  # type: ignore[arg-type]
    assert endpoint.state == "ready"
    endpoint.attach(GatedSpeaker(NoPcmSpeaker(), endpoint.gate))
    assert endpoint.state == "unsupported"


# --- Kokoro with a fake pipeline -----------------------------------------------------------------


class FakePipeline:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, text: str, voice: str) -> Iterator[tuple[int, str, NDArray[np.float32]]]:
        self.calls.append((text, voice))
        for _ in range(2):
            yield (0, "ps", np.full(2400, 0.25, dtype=np.float32))


def _kokoro(language: str = "pt", voice: str = "pf_dora") -> tuple[KokoroSpeaker, FakePipeline]:
    speaker = KokoroSpeaker(TTSConfig(engine="kokoro", language=language, kokoro_voice=voice))  # type: ignore[arg-type]
    pipeline = FakePipeline()
    speaker._pipeline = pipeline
    return speaker, pipeline


def test_kokoro_voices_follow_the_pipeline_language() -> None:
    voices = kokoro_voices("p", "pm_alex")
    assert [v.id for v in voices] == ["pm_alex", "pf_dora", "pm_santa"]
    assert voices[0].default and voices[0].name == "Alex (pm_alex)"
    assert voices[0].languages == ("pt-BR",)
    assert all(v.id.startswith("a") for v in kokoro_voices("a", "af_heart"))


def test_kokoro_synthesizes_pcm_without_playing() -> None:
    speaker, pipeline = _kokoro()
    player_feed: list[object] = []
    speaker._player.feed = player_feed.append  # type: ignore[method-assign,assignment]
    info = speaker.pcm_info()
    assert (info.name, info.sample_rate) == ("Kokoro", 24000)
    assert speaker.pcm_ready()
    stream = speaker.open_pcm("Olá.", voice="pm_santa")
    chunks = list(stream.chunks())
    assert [c.size for c in chunks] == [2400, 2400]
    assert pipeline.calls == [("Olá.", "pm_santa")]
    assert player_feed == []
    list(speaker.open_pcm("Oi.", voice=None).chunks())
    assert pipeline.calls[-1] == ("Oi.", "pf_dora")


def test_kokoro_close_ends_the_stream_and_unknown_voices_are_refused() -> None:
    speaker, _pipeline = _kokoro()
    stream = speaker.open_pcm("Olá.", voice=None)
    stream.close()
    assert list(stream.chunks()) == []
    with pytest.raises(SynthesisError) as caught:
        speaker.open_pcm("Olá.", voice="af_heart")
    assert caught.value.code == "unknown_voice"


def test_kokoro_that_failed_to_load_is_not_ready() -> None:
    speaker, _pipeline = _kokoro()
    speaker._load_failed = True
    with pytest.raises(SynthesisError) as caught:
        speaker.pcm_info()
    assert caught.value.code == "not_ready"


def test_kokoro_over_http(serve: Callable[..., ApiServer]) -> None:
    speaker, _pipeline = _kokoro()
    server = serve(GatedSpeaker(speaker, SpeechGate()))
    info = client(server).info()
    assert info.voices[0].id == "pf_dora"
    stream = client(server).speak("Olá.", voice="pm_alex")
    assert stream.sample_rate == 24000
    assert len(b"".join(stream.chunks())) == 2 * 2400 * 2


# --- the external provider, relayed ------------------------------------------------------------


@pytest.fixture
def provider() -> Iterator[object]:
    server = make_server(pace=0.0, expression_tags=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _external(provider: object, voice: str = "") -> ExternalSpeaker:
    url = f"http://127.0.0.1:{provider.server_port}"  # type: ignore[attr-defined]
    return ExternalSpeaker(TTSConfig(engine="external", external_url=url, external_voice=voice))


def test_external_relays_the_provider_voices_and_audio(serve: Callable[..., ApiServer], provider: object) -> None:
    server = serve(GatedSpeaker(_external(provider, voice="high"), SpeechGate()))
    info = client(server).info()
    assert info.name == "Reference test-tone provider"
    assert [v.id for v in info.voices] == ["low", "mid", "high"]
    assert info.supports("expression_tags")
    stream = client(server).speak("Hello world [laugh].", voice="low")
    assert stream.sample_rate == 24000
    assert len(b"".join(stream.chunks())) > 0
    assert provider.state.requests[-1]["voice"] == "low"  # type: ignore[attr-defined]
    b"".join(client(server).speak("Again.").chunks())
    assert provider.state.requests[-1]["voice"] == "high"  # type: ignore[attr-defined]


def test_external_maps_provider_errors(serve: Callable[..., ApiServer], provider: object) -> None:
    server = serve(GatedSpeaker(_external(provider), SpeechGate()))
    with pytest.raises(ProviderError) as caught:
        client(server).speak("Hi.", voice="nope")
    assert caught.value.code == "unknown_voice"


def test_external_unreachable_is_not_ready(serve: Callable[..., ApiServer]) -> None:
    speaker = ExternalSpeaker(TTSConfig(engine="external", external_url="http://127.0.0.1:9"))
    server = serve(GatedSpeaker(speaker, SpeechGate()))
    with pytest.raises(ProviderError) as caught:
        client(server).info()
    assert caught.value.kind == "not_ready"
    assert "not available" in caught.value.detail


def test_external_cancel_reaches_the_provider(provider: object) -> None:
    provider.state.pace = 1.0  # type: ignore[attr-defined]
    speaker = _external(provider)
    stream = speaker.open_pcm("one two three four five six seven eight nine ten", voice=None)
    next(stream.chunks())
    stream.close()
    assert _wait_until(lambda: provider.state.cancelled != [])  # type: ignore[attr-defined]


def test_external_text_over_the_provider_limit_is_text_too_long(serve: Callable[..., ApiServer]) -> None:
    provider = make_server(pace=0.0)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    try:
        speaker = ExternalSpeaker(TTSConfig(engine="external", external_url=f"http://127.0.0.1:{provider.server_port}"))
        with pytest.raises(SynthesisError) as caught:
            speaker.open_pcm("word " * 1000, voice=None)  # over the reference provider's 2000 characters
        assert caught.value.code == "text_too_long"
    finally:
        provider.shutdown()
        provider.server_close()


def test_external_upstream_failure_cuts_the_body_instead_of_ending_it() -> None:
    provider = make_server(pace=0.5)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    try:
        speaker = ExternalSpeaker(TTSConfig(engine="external", external_url=f"http://127.0.0.1:{provider.server_port}"))
        stream = speaker.open_pcm("one two three four five six seven eight nine ten", voice=None)
        chunks = stream.chunks()
        next(chunks)
        # The provider dies mid-sentence: its connection drops without the terminating chunk.
        provider.shutdown()
        provider.server_close()
        with provider.state.lock:
            events = list(provider.state.active.values())
        for event in events:
            event.set()
        with pytest.raises(ProviderError):
            list(chunks)
    finally:
        provider.shutdown()


class BrokenStream(FakeStream):
    """Produces two chunks and then fails, as a model that crashes mid-sentence."""

    def chunks(self) -> Iterator[NDArray[np.float32]]:
        yield from super().chunks()
        raise ProviderError("unreachable", "the audio stream broke")


class BreakingSynth(FakeSynth):
    def open_pcm(self, text: str, *, voice: str | None) -> FakeStream:
        stream = BrokenStream([np.full(1600, 0.5, dtype=np.float32) for _ in range(2)], 0.0)
        self.streams.append(stream)
        return stream


def test_a_failure_mid_stream_is_a_cut_body_not_a_normal_end(serve: Callable[..., ApiServer]) -> None:
    server = serve(BreakingSynth())
    stream = client(server).speak("Hello.")
    with pytest.raises(ProviderError) as caught:
        b"".join(stream.chunks())  # no terminating chunk arrived
    assert caught.value.kind == "unreachable"
    assert _wait_until(lambda: server.tts.gate.owner is None)


def test_a_normal_end_carries_the_terminating_chunk(serve: Callable[..., ApiServer]) -> None:
    server = serve(FakeSynth(chunks=3))
    stream = client(server).speak("Hello.")
    assert len(b"".join(stream.chunks())) == 3 * 1600 * 2  # no ProviderError: the body ended


def test_the_default_voice_id_is_the_configured_voice_only_for_a_synthesizer_without_voices(
    serve: Callable[..., ApiServer],
) -> None:
    plain = FakeSynth(voices=(PcmVoice("alto", "Alto", (), True),))
    server = serve(plain)
    b"".join(client(server).speak("Hi.", voice="alto").chunks())
    assert plain.opened[-1][1] == "alto"
    assert plain.voices[0].id == "alto"

    class NoVoices(FakeSynth):
        def pcm_info(self) -> PcmInfo:
            return PcmInfo("Fake voice", 16000, (), ("streaming",))

    none = NoVoices()
    server = serve(none)
    b"".join(client(server).speak("Hi.", voice="default").chunks())
    assert none.opened[-1][1] is None  # the synthetic `default` means the configured voice

    named = FakeSynth(voices=(PcmVoice("default", "A real voice called default", (), True),))
    server = serve(named)
    b"".join(client(server).speak("Hi.", voice="default").chunks())
    assert named.opened[-1][1] == "default"  # a real voice with that id reaches the synthesizer


def test_a_cancel_while_the_stream_opens_drops_it_and_frees_the_gate() -> None:
    opening, release = threading.Event(), threading.Event()

    class SlowOpen(FakeSynth):
        def open_pcm(self, text: str, *, voice: str | None) -> FakeStream:
            opening.set()
            release.wait(5)
            return super().open_pcm(text, voice=voice)

    synth = SlowOpen()
    endpoint = TtsEndpoint()
    endpoint.attach(synth)
    replies: list[object] = []
    speak = threading.Thread(
        target=lambda: replies.append(endpoint.handle("POST", "/tts/v1/speak", {"request_id": "r1", "text": "Hi."}))
    )
    speak.start()
    assert opening.wait(2)
    assert endpoint.handle("POST", "/tts/v1/cancel", {"request_id": "r1"}).status == 200
    release.set()
    speak.join(3)
    assert not speak.is_alive()
    assert getattr(replies[0], "stream", None) is None
    assert synth.streams[0].closed.is_set()
    assert endpoint.gate.owner is None  # the next request is not refused as busy
