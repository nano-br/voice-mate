from __future__ import annotations

import asyncio
import concurrent.futures
import sys
import threading

from app.core.audio_feedback import AudioFeedback
from app.core.chat import ChatBackend
from app.features.claude.sentence_buffer import SentenceBuffer
from app.features.tts.base import TextToSpeech
from app.i18n import _
from app.platform.clipboard import ClipboardWriter, PyperclipWriter


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
        clipboard: ClipboardWriter | None = None,
    ) -> None:
        self._runtime = runtime
        self._audio = audio
        self._speaker = speaker
        self._timeout_seconds = timeout_seconds
        self._clipboard: ClipboardWriter = clipboard if clipboard is not None else PyperclipWriter()
        self._lock = threading.Lock()
        self._busy = False
        self._cancelled = False

    def handle(self, text: str) -> None:
        with self._lock:
            self._busy = True
            self._cancelled = False
        transcription_preview = text[:200] + ("..." if len(text) > 200 else "")
        print(_("[VoiceMate] ✓ Transcription: {preview}").format(preview=transcription_preview))
        self._clipboard.copy(text)
        print(_("[VoiceMate] 📋 Transcription copied to clipboard."))
        try:
            print(_("[VoiceMate] 🤖 Calling Claude..."))
            # Com TTS ativo: streaming por frase (fala a 1ª frase antes de a resposta
            # terminar). Sem TTS: coleta completa + beep (caminho original).
            if self._speaker.is_active():
                response = self._stream_and_speak(text)
            else:
                response = self._runtime.send_and_collect(text, timeout=self._timeout_seconds)
            if self._is_cancelled():
                print(_("[VoiceMate] ✗ Claude response discarded (cancelled)."))
                return
            if not response:
                print(_("[VoiceMate] Claude returned an empty response."))
                return
            response_preview = response[:200] + ("..." if len(response) > 200 else "")
            print(_("[VoiceMate] 💬 Claude: {preview}").format(preview=response_preview))
            self._clipboard.copy(response)
            print(_("[VoiceMate] 📋 Claude response copied to clipboard."))
            if not self._speaker.is_active():
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

    def _stream_and_speak(self, text: str) -> str:
        """Consome o stream do Claude, fala frase a frase e devolve o texto completo.

        `speak()` enfileira cada frase no player persistente e retorna; o áudio toca
        enquanto a próxima frase é gerada (pipeline, sem buracos). `wait_done()` ao
        final aguarda a reprodução terminar. O texto completo é acumulado p/ o clipboard.
        """
        buffer = SentenceBuffer()
        parts: list[str] = []
        for delta in self._runtime.stream(text, timeout=self._timeout_seconds):
            if self._is_cancelled():
                break
            parts.append(delta)
            if not self._speak_all(buffer.feed(delta)):
                break
        if not self._is_cancelled():
            tail = buffer.flush()
            if tail:
                self._speaker.speak(tail)
            self._speaker.wait_done()
        return "".join(parts)

    def _speak_all(self, sentences: list[str]) -> bool:
        """Fala cada frase em ordem; retorna False se cancelado no meio."""
        for sentence in sentences:
            if self._is_cancelled():
                return False
            self._speaker.speak(sentence)
        return True

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

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
