import numpy as np
import sounddevice as sd

_SAMPLE_RATE = 44100
_FADE_MS = 10


def _play_tone(frequency: float, duration_ms: int, volume: float = 0.5) -> None:
    """Generate and play a sine wave with fade-in/fade-out."""
    samples = int(_SAMPLE_RATE * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, samples, dtype=np.float32)
    wave = (volume * np.sin(2 * np.pi * frequency * t)).astype(np.float32)

    # Fade-in/fade-out to avoid clicks
    fade_samples = int(_SAMPLE_RATE * _FADE_MS / 1000)
    if fade_samples > 0 and len(wave) > 2 * fade_samples:
        fade_in = np.linspace(0, 1, fade_samples, dtype=np.float32)
        fade_out = np.linspace(1, 0, fade_samples, dtype=np.float32)
        wave[:fade_samples] *= fade_in
        wave[-fade_samples:] *= fade_out

    sd.play(wave, samplerate=_SAMPLE_RATE)
    sd.wait()


class AudioFeedback:
    """Cross-platform audio feedback using sounddevice + numpy."""

    @staticmethod
    def recording_started() -> None:
        """Rising tone — signals the start of recording."""
        _play_tone(660, 120)

    @staticmethod
    def transcription_complete() -> None:
        """Two short tones — signals transcription complete."""
        _play_tone(880, 100)
        _play_tone(1100, 150)

    @staticmethod
    def timeout_warning() -> None:
        """Soft pulse — warns that the timeout is near."""
        _play_tone(440, 200)

    @staticmethod
    def error() -> None:
        """Low tone — signals an error."""
        _play_tone(300, 300)

    @staticmethod
    def ai_response_ready() -> None:
        """Rising C5-E5-G5 triad — signals the AI has responded."""
        _play_tone(523, 90)
        _play_tone(659, 90)
        _play_tone(784, 180)
