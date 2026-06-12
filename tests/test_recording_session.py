from __future__ import annotations

import threading
import time

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config
from app.core.recording_session import RecordingSession


class FakeRecorder:
    def __init__(self) -> None:
        self._recording = False
        self._lock = threading.Lock()
        self.start_calls = 0
        self.stop_calls = 0
        self.next_audio: NDArray[np.float32] | None = np.zeros(16000, dtype=np.float32)

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(self) -> bool:
        with self._lock:
            if self._recording:
                return False
            self._recording = True
            self.start_calls += 1
        return True

    def stop(self) -> NDArray[np.float32] | None:
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            self.stop_calls += 1
        return self.next_audio


class FakeTranscriber:
    def __init__(self, text: str = "texto transcrito") -> None:
        self.text = text
        self.calls: list[NDArray[np.float32]] = []

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        self.calls.append(audio)
        return self.text


class FakeAudio:
    def __init__(self) -> None:
        self.recording_started_calls = 0
        self.transcription_complete_calls = 0
        self.timeout_warning_calls = 0
        self.error_calls = 0

    def recording_started(self) -> None:
        self.recording_started_calls += 1

    def transcription_complete(self) -> None:
        self.transcription_complete_calls += 1

    def timeout_warning(self) -> None:
        self.timeout_warning_calls += 1

    def error(self) -> None:
        self.error_calls += 1


class FakeHandler:
    def __init__(self, block_event: threading.Event | None = None) -> None:
        self.handle_calls: list[str] = []
        self.cancel_calls = 0
        self.close_calls = 0
        self.block_event = block_event
        self.handle_started = threading.Event()
        self.handle_done = threading.Event()
        self._busy = False
        self._lock = threading.Lock()

    def handle(self, text: str) -> None:
        with self._lock:
            self._busy = True
        self.handle_calls.append(text)
        self.handle_started.set()
        if self.block_event is not None:
            self.block_event.wait(timeout=2.0)
        with self._lock:
            self._busy = False
        self.handle_done.set()

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def cancel_in_flight(self) -> None:
        self.cancel_calls += 1
        if self.block_event is not None:
            self.block_event.set()

    def close(self) -> None:
        self.close_calls += 1


def _make_session(
    handlers: dict[str, FakeHandler] | None = None,
    transcriber: FakeTranscriber | None = None,
) -> tuple[RecordingSession, FakeRecorder, FakeTranscriber, FakeAudio, dict[str, FakeHandler]]:
    recorder = FakeRecorder()
    transcriber = transcriber or FakeTranscriber()
    audio = FakeAudio()
    config = Config(max_recording_seconds=600)
    handlers = handlers or {"clipboard": FakeHandler()}
    session = RecordingSession(
        recorder=recorder,  # type: ignore[arg-type]
        transcriber=transcriber,  # type: ignore[arg-type]
        audio=audio,  # type: ignore[arg-type]
        config=config,
        handlers=handlers,  # type: ignore[arg-type]
        default_handler_id=next(iter(handlers)),
    )
    return session, recorder, transcriber, audio, handlers


def test_toggle_idle_starts_recording() -> None:
    session, recorder, _, audio, _ = _make_session()

    session.toggle("clipboard")

    assert recorder.start_calls == 1
    assert audio.recording_started_calls == 1
    assert recorder.is_recording


def test_toggle_recording_dispatches_to_handler_of_stop() -> None:
    handlers = {"clipboard": FakeHandler(), "claude_chat": FakeHandler()}
    session, recorder, _, _, _ = _make_session(handlers=handlers)

    session.toggle("clipboard")  # start
    session.toggle("claude_chat")  # stop com handler diferente
    assert handlers["claude_chat"].handle_started.wait(timeout=2.0)
    assert handlers["claude_chat"].handle_done.wait(timeout=2.0)

    assert handlers["clipboard"].handle_calls == []
    assert handlers["claude_chat"].handle_calls == ["texto transcrito"]
    assert recorder.stop_calls == 1


def test_toggle_unknown_handler_id_is_noop() -> None:
    session, recorder, _, _, _ = _make_session()
    session.toggle("nope")
    assert recorder.start_calls == 0


def test_no_audio_skips_handler_call() -> None:
    handler = FakeHandler()
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler})
    recorder.next_audio = None

    session.toggle("clipboard")
    session.toggle("clipboard")
    # Espera o stop thread completar
    deadline = time.monotonic() + 2.0
    while recorder.stop_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)

    assert handler.handle_calls == []


def test_no_transcription_text_skips_handler_call() -> None:
    handler = FakeHandler()
    transcriber = FakeTranscriber(text="")
    session, recorder, _, _, _ = _make_session(handlers={"clipboard": handler}, transcriber=transcriber)

    session.toggle("clipboard")
    session.toggle("clipboard")
    deadline = time.monotonic() + 2.0
    while recorder.stop_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)

    assert handler.handle_calls == []


def test_toggle_during_processing_cancels_and_restarts() -> None:
    block = threading.Event()
    handler = FakeHandler(block_event=block)
    session, recorder, _, _, _ = _make_session(handlers={"claude_chat": handler})

    session.toggle("claude_chat")  # start
    session.toggle("claude_chat")  # stop → entra processing → handler.handle bloqueia em block
    assert handler.handle_started.wait(timeout=2.0)
    assert handler.is_busy()

    # terceiro toggle: cancela IA pendente e inicia nova gravação
    session.toggle("claude_chat")

    # cancel_in_flight foi chamado e o handler.handle retornou
    assert handler.cancel_calls == 1
    assert handler.handle_done.wait(timeout=2.0)
    # recorder começou nova gravação
    assert recorder.start_calls == 2
    assert recorder.is_recording


def test_transcriber_exception_recovers_to_idle() -> None:
    """Exceção na transcrição não pode prender a sessão em `processing`:
    o estado volta a idle, audio.error() toca e o próximo toggle grava normal."""

    class BoomTranscriber(FakeTranscriber):
        def transcribe(self, audio: NDArray[np.float32]) -> str:
            raise RuntimeError("whisper explodiu")

    handler = FakeHandler()
    session, recorder, _, audio, _ = _make_session(handlers={"clipboard": handler}, transcriber=BoomTranscriber())

    session.toggle("clipboard")  # start
    session.toggle("clipboard")  # stop → transcribe levanta na thread
    deadline = time.monotonic() + 2.0
    while audio.error_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert audio.error_calls == 1
    assert handler.handle_calls == []

    # Sessão se recuperou: novo toggle inicia gravação de novo (estado idle).
    session.toggle("clipboard")
    assert recorder.start_calls == 2
    assert recorder.is_recording


def test_handler_close_not_called_by_session() -> None:
    """RecordingSession não fecha handlers — fechamento é responsabilidade do main."""
    handler = FakeHandler()
    session, _, _, _, _ = _make_session(handlers={"clipboard": handler})
    session.toggle("clipboard")
    session.toggle("clipboard")
    assert handler.handle_done.wait(timeout=2.0)
    assert handler.close_calls == 0
