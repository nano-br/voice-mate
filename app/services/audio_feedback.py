import numpy as np
import sounddevice as sd

_SAMPLE_RATE = 44100
_FADE_MS = 10


def _play_tone(frequency: float, duration_ms: int, volume: float = 0.5) -> None:
    """Gera e toca uma onda senoidal com fade-in/fade-out."""
    samples = int(_SAMPLE_RATE * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, samples, dtype=np.float32)
    wave = (volume * np.sin(2 * np.pi * frequency * t)).astype(np.float32)

    # Fade-in/fade-out para evitar cliques
    fade_samples = int(_SAMPLE_RATE * _FADE_MS / 1000)
    if fade_samples > 0 and len(wave) > 2 * fade_samples:
        fade_in = np.linspace(0, 1, fade_samples, dtype=np.float32)
        fade_out = np.linspace(1, 0, fade_samples, dtype=np.float32)
        wave[:fade_samples] *= fade_in
        wave[-fade_samples:] *= fade_out

    sd.play(wave, samplerate=_SAMPLE_RATE)
    sd.wait()


class AudioFeedback:
    """Feedback sonoro cross-platform usando sounddevice + numpy."""

    @staticmethod
    def recording_started() -> None:
        """Tom ascendente — indica início da gravação."""
        _play_tone(660, 120)

    @staticmethod
    def transcription_complete() -> None:
        """Dois tons curtos — indica transcrição concluída."""
        _play_tone(880, 100)
        _play_tone(1100, 150)

    @staticmethod
    def timeout_warning() -> None:
        """Pulso suave — aviso de timeout próximo."""
        _play_tone(440, 200)

    @staticmethod
    def error() -> None:
        """Tom grave — indica erro."""
        _play_tone(300, 300)
