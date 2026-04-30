import threading

import numpy as np
import pyperclip
from numpy.typing import NDArray

from app.core.config import Config
from app.services.audio_feedback import AudioFeedback
from app.services.recorder import Recorder
from app.services.transcriber import Transcriber


class RecordingSession:
    """Gerencia o ciclo de gravação com timeout e auto-transcrição."""

    def __init__(
        self,
        recorder: Recorder,
        transcriber: Transcriber,
        audio: AudioFeedback,
        config: Config,
    ) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._audio = audio
        self._sample_rate = config.sample_rate
        self._max_seconds = config.max_recording_seconds
        self._warning_percent = config.timeout_warning_percent
        self._lock = threading.Lock()
        self._warning_timer: threading.Timer | None = None
        self._timeout_timer: threading.Timer | None = None

    def toggle(self) -> None:
        """Alterna entre iniciar e parar gravação."""
        with self._lock:
            if not self._recorder.is_recording:
                self._start()
            else:
                self._stop_async()

    def _start(self) -> None:
        if not self._recorder.start():
            return
        self._audio.recording_started()
        print("[VoiceMate] 🎙  Gravando... (pressione novamente para parar)")
        self._schedule_timers()

    def _stop_async(self) -> None:
        self._cancel_timers()
        threading.Thread(target=self._stop_and_transcribe, daemon=True).start()

    def _schedule_timers(self) -> None:
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
            self._cancel_timers()
        self._stop_and_transcribe()

    def _stop_and_transcribe(self) -> None:
        result: NDArray[np.float32] | None = self._recorder.stop()
        if result is None:
            print("[VoiceMate] Nenhum áudio capturado.")
            return

        duration = len(result) / self._sample_rate
        print(f"[VoiceMate] ⏳ Transcrevendo {duration:.1f}s de áudio...")

        text = self._transcriber.transcribe(result)
        if text:
            pyperclip.copy(text)
            self._audio.transcription_complete()
            preview = text[:100] + ("..." if len(text) > 100 else "")
            print(f"[VoiceMate] ✓ Copiado: {preview}")
        else:
            print("[VoiceMate] Nenhuma fala detectada.")
