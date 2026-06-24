import sys
import threading
import time
import traceback

import numpy as np
from numpy.typing import NDArray

from app.core.audio_feedback import AudioFeedback
from app.core.config import Config
from app.core.recorder import Recorder
from app.core.session_status import SessionState, SessionStatus, ToggleAction, ToggleOutcome
from app.core.transcription_backend import TranscriptionBackend
from app.core.transcription_handler import TranscriptionHandler


class RecordingSession:
    """Gerencia o ciclo gravação → transcrição → handler com máquina de estado.

    Estados: idle → recording → processing → idle. Trigger em `processing`
    cancela o handler ativo e inicia uma nova gravação imediatamente.
    O `handler_id` passado em `toggle()` é usado apenas quando o trigger
    PARA a gravação (decide o destino do texto).

    `toggle()` devolve um `ToggleOutcome` na hora (a decisão start/stop é
    síncrona sob lock — só a transcrição é assíncrona), para o gatilho saber o
    que aconteceu. Se um `SessionStatus` for injetado, as transições de estado
    são publicadas nele (estado vivo consultável pelos consumidores).
    """

    def __init__(
        self,
        recorder: Recorder,
        transcriber: TranscriptionBackend,
        audio: AudioFeedback,
        config: Config,
        handlers: dict[str, TranscriptionHandler],
        default_handler_id: str = "clipboard",
        status: SessionStatus | None = None,
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
        self._status = status
        self._lock = threading.Lock()
        self._warning_timer: threading.Timer | None = None
        self._timeout_timer: threading.Timer | None = None
        self._state: SessionState = "idle"
        self._stop_handler_id: str | None = None
        self._active_handler_id: str | None = None
        self._slow_warning_shown = False
        # op_seq: a sessão é a autoridade. Cada gravação que COMEÇA abre uma
        # operação nova; o STOP continua a mesma (vai a processing).
        self._op_counter = 0
        self._op_seq = 0
        self._op_flow: str | None = None
        self._op_client_id: str | None = None

    def toggle(self, handler_id: str, client_id: str | None = None) -> ToggleOutcome | None:
        if handler_id not in self._handlers:
            print(f"[VoiceMate] ⚠ Handler desconhecido: {handler_id}")
            return None
        handler_to_cancel: TranscriptionHandler | None = None
        outcome: ToggleOutcome | None = None
        with self._lock:
            state = self._state
            if state == "idle":
                self._start_locked(handler_id, client_id)
                outcome = self._outcome_locked("started")
            elif state == "recording":
                self._stop_handler_id = handler_id
                self._cancel_timers()
                self._state = "processing"
                self._publish_state_locked("processing")
                outcome = self._outcome_locked("stopped")
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
                    self._start_locked(handler_id, client_id)
                    outcome = self._outcome_locked("restarted")
                else:
                    outcome = self._outcome_locked("started")
        return outcome

    def _outcome_locked(self, action: ToggleAction) -> ToggleOutcome:
        return ToggleOutcome(
            action=action,
            op_seq=self._op_seq,
            state=self._state,
            flow=self._op_flow or self._default_handler_id,
        )

    def _publish_state_locked(self, state: SessionState) -> None:
        if self._status is not None:
            self._status.set_operation(self._op_seq, state, self._op_flow, self._op_client_id)

    def _start_locked(self, handler_id: str, client_id: str | None) -> None:
        if not self._recorder.start():
            self._state = "idle"
            return
        self._state = "recording"
        self._op_counter += 1
        self._op_seq = self._op_counter
        self._op_flow = handler_id
        self._op_client_id = client_id
        self._publish_state_locked("recording")
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
            self._publish_state_locked("processing")
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
        op_seq: int | None = None
        with self._lock:
            if self._state == "processing" and self._active_handler_id == stop_id:
                self._state = "idle"
                self._active_handler_id = None
                op_seq = self._op_seq
        # Publica idle FORA do lock da sessão (o hub tem o próprio lock) e só se
        # esta finalização foi quem realmente voltou a idle — mark_idle ignora se
        # uma nova operação já abriu por cima (sem corrida).
        if op_seq is not None and self._status is not None:
            self._status.mark_idle(op_seq)
