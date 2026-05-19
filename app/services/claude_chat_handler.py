from __future__ import annotations

import asyncio
import sys
import threading

import pyperclip

from app.services.audio_feedback import AudioFeedback
from app.services.claude_runtime import ClaudeRuntime
from app.services.tts import TextToSpeech


class ClaudeChatHandler:
    """Envia o texto transcrito para o Claude e copia a resposta no clipboard.

    Mantém uma única conversa multi-turn enquanto o app vive (o `ClaudeRuntime`
    mantém o `ClaudeSDKClient` aberto). Se um `TextToSpeech` ativo for injetado,
    a resposta também é falada via TTS (e o beep tradicional é suprimido).
    Suporta cancelamento do turno em andamento via `cancel_in_flight()` —
    interrompe a chamada à IA e a reprodução do TTS simultaneamente.
    """

    def __init__(
        self,
        runtime: ClaudeRuntime,
        audio: AudioFeedback,
        speaker: TextToSpeech,
    ) -> None:
        self._runtime = runtime
        self._audio = audio
        self._speaker = speaker
        self._lock = threading.Lock()
        self._busy = False
        self._cancelled = False

    def handle(self, text: str) -> None:
        with self._lock:
            self._busy = True
            self._cancelled = False
        transcription_preview = text[:200] + ("..." if len(text) > 200 else "")
        print(f"[VoiceMate] ✓ Transcrição: {transcription_preview}")
        pyperclip.copy(text)
        print("[VoiceMate] 📋 Transcrição copiada para o clipboard.")
        try:
            print("[VoiceMate] 🤖 Chamando Claude...")
            response = self._runtime.send_and_collect(text)
            with self._lock:
                cancelled = self._cancelled
            if cancelled:
                print("[VoiceMate] ✗ Resposta do Claude descartada (cancelada).")
                return
            if not response:
                print("[VoiceMate] Claude retornou resposta vazia.")
                return
            response_preview = response[:200] + ("..." if len(response) > 200 else "")
            print(f"[VoiceMate] 💬 Claude: {response_preview}")
            pyperclip.copy(response)
            print("[VoiceMate] 📋 Resposta do Claude copiada para o clipboard.")
            if self._speaker.is_active():
                self._speaker.speak(response)
            else:
                self._audio.ai_response_ready()
        except asyncio.CancelledError:
            print("[VoiceMate] ✗ Chamada ao Claude cancelada.")
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
