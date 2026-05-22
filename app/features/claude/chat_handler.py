from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading

import pyperclip

from app.core.audio_feedback import AudioFeedback
from app.core.chat import ChatBackend
from app.features.tts.base import TextToSpeech
from app.i18n import _


class ClaudeChatHandler:
    """Envia o texto transcrito para o Claude e copia a resposta no clipboard.

    Depende de qualquer `ChatBackend` (Protocol). `ClaudeRuntime` é o
    backend padrão hoje, mas outros (Codex, Antigravity, etc.) podem ser
    injetados sem mudança aqui. Se um `TextToSpeech` ativo for injetado,
    a resposta também é falada via TTS (e o beep tradicional é suprimido).
    Suporta cancelamento do turno em andamento via `cancel_in_flight()` —
    interrompe a chamada à IA e a reprodução do TTS simultaneamente.
    `timeout_seconds` é defesa em profundidade contra travamento do backend.
    """

    def __init__(
        self,
        runtime: ChatBackend,
        audio: AudioFeedback,
        speaker: TextToSpeech,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._runtime = runtime
        self._audio = audio
        self._speaker = speaker
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._busy = False
        self._cancelled = False

    def handle(self, text: str) -> None:
        with self._lock:
            self._busy = True
            self._cancelled = False
        transcription_preview = text[:200] + ("..." if len(text) > 200 else "")
        print(_("[VoiceMate] ✓ Transcription: {preview}").format(preview=transcription_preview))
        pyperclip.copy(text)
        print(_("[VoiceMate] 📋 Transcription copied to clipboard."))
        try:
            print(_("[VoiceMate] 🤖 Calling Claude..."))
            response = self._runtime.send_and_collect(text, timeout=self._timeout_seconds)
            with self._lock:
                cancelled = self._cancelled
            if cancelled:
                print(_("[VoiceMate] ✗ Claude response discarded (cancelled)."))
                return
            if not response:
                print(_("[VoiceMate] Claude returned an empty response."))
                return
            response_preview = response[:200] + ("..." if len(response) > 200 else "")
            print(_("[VoiceMate] 💬 Claude: {preview}").format(preview=response_preview))
            pyperclip.copy(response)
            print(_("[VoiceMate] 📋 Claude response copied to clipboard."))
            if self._speaker.is_active():
                self._speaker.speak(response)
            else:
                self._audio.ai_response_ready()
        except asyncio.CancelledError:
            print("[VoiceMate] ✗ Chamada ao Claude cancelada.")
        except (TimeoutError, concurrent.futures.TimeoutError):
            print(
                f"[VoiceMate] ⏱ Claude excedeu timeout ({self._timeout_seconds:.0f}s), descartando turno.",
                file=sys.stderr,
            )
            self._runtime.interrupt()
            self._audio.error()
        except Exception as exc:  # noqa: BLE001
            print(f"[VoiceMate] ❌ Erro ao falar com o Claude: {exc}", file=sys.stderr)
            self._audio.error()
        finally:
            with self._lock:
                self._busy = False
                self._cancelled = False

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def cancel_in_flight(self) -> None:
        with self._lock:
            if not self._busy:
                return
            self._cancelled = True
        self._runtime.interrupt()
        self._speaker.stop()

    def close(self) -> None:
        self._runtime.stop()
        self._speaker.close()
