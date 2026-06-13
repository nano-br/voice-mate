import sys
import threading
import time
import traceback
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from app.core.audio_feedback import AudioFeedback
from app.core.config import Config
from app.core.recorder import Recorder
from app.core.transcription_backend import TranscriptionBackend
from app.core.transcription_handler import TranscriptionHandler

SessionState = Literal["idle", "recording", "processing"]


class RecordingSession:
    """Gerencia o ciclo gravação → transcrição → handler com máquina de estado.

    Estados: idle → recording → processing → idle. Trigger em `processing`
    cancela o handler ativo e inicia uma nova gravação imediatamente.
    O `handler_id` passado em `toggle()` é usado apenas quando o trigger
    PARA a gravação (decide o destino do texto).
    """

    def __init__(
        self,
        recorder: Recorder,
        transcriber: TranscriptionBackend,
        audio: AudioFeedback,
        config: Config,
        handlers: dict[str, TranscriptionHandler],
        default_handler_id: str = "clipboard",
    ) -> None:
        if not handlers:
            raise ValueError("RecordingSession precisa de ao menos um handler")
        if default_handler_id not in handlers:
            raise ValueError(f"default_handler_id '{default_handler_id}' não está em handlers")
        self._recorder = recorder
        self._transcriber = transcriber
        self._audio = audio
        self._sample_rate = config.sample_rate
        self._max_seconds = config.max_recording_seconds
        self._warning_percent = config.timeout_warning_percent
        self._handlers = handlers
        self._default_handler_id = default_handler_id
        self._lock = threading.Lock()
        self._warning_timer: threading.Timer | None = None
        self._timeout_timer: threading.Timer | None = None
        self._state: SessionState = "idle"
        self._stop_handler_id: str | None = None
        self._active_handler_id: str | None = None
        self._slow_warning_shown = False

    def toggle(self, handler_id: str) -> None:
        if handler_id not in self._handlers:
            print(f"[VoiceMate] ⚠ Handler desconhecido: {handler_id}")
            return
        handler_to_cancel: TranscriptionHandler | None = None
        with self._lock:
            state = self._state
            if state == "idle":
                self._start_locked()
            elif state == "recording":
                self._stop_handler_id = handler_id
                self._cancel_timers()
                self._state = "processing"
                threading.Thread(target=self._stop_and_dispatch, daemon=True).start()
            elif state == "processing":
                active = self._active_handler_id
                if active is not None:
                    handler_to_cancel = self._handlers[active]
                self._active_handler_id = None
        if state == "processing":
            # Solta o lock antes de chamar cancel_in_flight (que pode bloquear
            # brevemente em I/O) e antes de iniciar o recorder.
            if handler_to_cancel is not None:
                handler_to_cancel.cancel_in_flight()
            with self._lock:
                if self._state == "processing":
                    self._start_locked()

    def _start_locked(self) -> None:
        if not self._recorder.start():
            self._state = "idle"
            return
        self._state = "recording"
        self._audio.recording_started()
        print("[VoiceMate] 🎙  Gravando... (pressione para parar)")
        self._schedule_timers_locked()

    def _schedule_timers_locked(self) -> None:
        warning_at = self._max_seconds * self._warning_percent
        self._warning_timer = threading.Timer(warning_at, self._on_warning)
        self._warning_timer.daemon = True
        self._warning_timer.start()
        self._timeout_timer = threading.Timer(float(self._max_seconds), self._on_timeout)
        self._timeout_timer.daemon = True
        self._timeout_timer.start()

    def _cancel_timers(self) -> None:
        if self._warning_timer is not None:
            self._warning_timer.cancel()
            self._warning_timer = None
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

    def _on_warning(self) -> None:
        remaining = self._max_seconds * (1 - self._warning_percent)
        print(f"[VoiceMate] ⚠ Gravação será encerrada em {remaining:.0f}s")
        self._audio.timeout_warning()

    def _on_timeout(self) -> None:
        print("[VoiceMate] ⏰ Tempo máximo atingido. Encerrando gravação...")
        with self._lock:
            if self._state != "recording":
                return
            self._cancel_timers()
            self._stop_handler_id = self._default_handler_id
            self._state = "processing"
        self._stop_and_dispatch()

    def _stop_and_dispatch(self) -> None:
        """Para a gravação, transcreve e despacha — roda em thread própria.

        Qualquer exceção aqui é logada (traceback) e o estado SEMPRE volta a
        idle: uma thread daemon que morre calada deixaria a sessão presa em
        `processing` e o toggle "morto" sem nenhuma pista no log.
        """
        stop_id: str | None = None
        try:
            result: NDArray[np.float32] | None = self._recorder.stop()
            with self._lock:
                stop_id = self._stop_handler_id
                self._stop_handler_id = None
                if self._state != "processing":
                    # Usuário cancelou e iniciou nova gravação; abortamos silenciosamente.
                    return
                if stop_id is None:
                    self._state = "idle"
                    return
                self._active_handler_id = stop_id

            if result is None:
                print("[VoiceMate] Nenhum áudio capturado.")
                return

            duration = len(result) / self._sample_rate
            print(f"[VoiceMate] ⏳ Transcrevendo {duration:.1f}s de áudio...")
            started_at = time.perf_counter()
            text = self._transcriber.transcribe(result)
            self._warn_if_slow(duration, time.perf_counter() - started_at)
            if not text:
                print("[VoiceMate] Nenhuma fala detectada.")
                return

            self._handlers[stop_id].handle(text)
        except Exception:  # noqa: BLE001 — fronteira de thread: logar + se recuperar
            print("[VoiceMate] ❌ Erro ao processar a gravação:", file=sys.stderr)
            traceback.print_exc()
            try:
                self._audio.error()
            except Exception:  # noqa: BLE001, S110 — beep é best-effort
                pass
        finally:
            if stop_id is not None:
                self._finish_processing_locked(stop_id)

    def _warn_if_slow(self, audio_seconds: float, elapsed: float) -> None:
        """Detecta backend sem GPU (fallback silencioso p/ CPU/software).

        Transcrição saudável na GPU roda bem abaixo do tempo real; 3× o tempo
        do áudio (com piso de 5s p/ absorver warmup) indica que o backend está
        rodando em CPU/Vulkan-software. Avisa uma vez por sessão.
        """
        if self._slow_warning_shown or elapsed <= max(5.0, 3.0 * audio_seconds):
            return
        self._slow_warning_shown = True
        ratio = elapsed / audio_seconds if audio_seconds > 0 else float("inf")
        print(
            f"[VoiceMate] ⚠ Transcrição {ratio:.0f}× mais lenta que o áudio "
            f"({elapsed:.0f}s p/ {audio_seconds:.0f}s) — o backend provavelmente está SEM GPU. "
            "Rode `make doctor` para diagnóstico.",
            file=sys.stderr,
        )

    def _finish_processing_locked(self, stop_id: str) -> None:
        with self._lock:
            if self._state == "processing" and self._active_handler_id == stop_id:
                self._state = "idle"
                self._active_handler_id = None
