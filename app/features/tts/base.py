from __future__ import annotations

from typing import Protocol


class TextToSpeech(Protocol):
    """Orquestrador de Text-to-Speech.

    Implementações concretas (VoxCPM, Edge-TTS, pyttsx3, etc.) ficam isoladas
    em módulos próprios e podem ser trocadas sem mexer no fluxo de chamada.
    """

    def is_active(self) -> bool:
        """Indica se o speaker está pronto para falar.

        O handler usa esse sinal para decidir se chama `speak` ou cai no
        feedback sonoro alternativo (beep). `False` em NullSpeaker e em
        speakers cujo bootstrap falhou.
        """
        ...

    def speak(self, text: str) -> None:
        """Sintetiza e reproduz o texto. Bloqueia até o fim ou até `stop()`."""
        ...

    def warmup(self) -> None:
        """Pré-aquece o speaker (carrega modelo/tuna kernels) fora do 1º turno.

        Chamado em background no startup para a 1ª frase já sair realtime.
        Idempotente. No-op em NullSpeaker e em speakers desligados.
        """
        ...

    def wait_done(self, timeout: float | None = None) -> bool:
        """Espera o áudio enfileirado terminar de tocar (fim do turno).

        Para speakers com pipeline (`speak()` enfileira e retorna), o handler
        chama isto ao final para aguardar a reprodução. Speakers que já bloqueiam
        em `speak()` podem retornar True imediatamente.
        """
        ...

    def stop(self) -> None:
        """Interrompe imediatamente a reprodução em andamento."""
        ...

    def close(self) -> None:
        """Libera recursos (modelo, streams)."""
        ...


class NullSpeaker:
    """Speaker no-op — usado quando TTS está desligado ou falhou ao iniciar."""

    def is_active(self) -> bool:
        return False

    def speak(self, text: str) -> None:
        return None

    def warmup(self) -> None:
        return None

    def wait_done(self, timeout: float | None = None) -> bool:
        return True

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None
