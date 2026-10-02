"""Sound cues: presets rendered to WAV files with the stdlib, custom WAV files re-rendered.

Every file has its gain (master volume x cue volume) baked in and a content-hash name, so
a changed setting produces a NEW file and a sound still playing keeps its old file open
(docs/companion-app.md, "Sound cues"). Playback itself is platform code (`winsound`,
`pw-play`/`paplay`); this module only hands it paths.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import sys
import threading
import time
import wave
from array import array
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from app.companion.contract import CUE_NAMES, CompanionSettings, CueName, CuePreset, CueSettings
from app.i18n import _

log = logging.getLogger(__name__)

SAMPLE_RATE: Final = 44100
MAX_CUSTOM_SECONDS: Final = 10.0
CueFileProblem = Literal["missing", "format", "too_long"]


class CueFileError(Exception):
    def __init__(self, problem: CueFileProblem, detail: str = "") -> None:
        super().__init__(f"{problem}: {detail}" if detail else problem)
        self.problem: CueFileProblem = problem


@dataclass(frozen=True)
class _Tone:
    freq: float
    ms: float


# The engine's own tones (app/core/audio_feedback.py), plus the new `transcribing` cue.
_CLASSIC_TONES: Final[dict[CueName, tuple[_Tone, ...]]] = {
    "start": (_Tone(660, 120),),
    "transcribing": (_Tone(523, 70),),
    "ready": (_Tone(880, 100), _Tone(1100, 150)),
    "ai_ready": (_Tone(523, 90), _Tone(659, 90), _Tone(784, 180)),
    "warning": (_Tone(440, 200),),
    "error": (_Tone(300, 300),),
}
_CLASSIC_AMPLITUDE: Final = 0.5  # the engine plays at 0.5
_CLASSIC_FADE_MS: Final = 10.0
_SOFT_AMPLITUDE: Final = 0.3
_SOFT_STRETCH: Final = 1.25
_SOFT_FADE_SHARE: Final = 0.4  # fade in and fade out each take 40% of the tone

# Click patterns: (frequency, silence before this click in ms).
_CLICK_PATTERNS: Final[dict[CueName, tuple[tuple[float, float], ...]]] = {
    "start": ((2000, 0),),
    "transcribing": ((1500, 0),),
    "ready": ((2000, 0), (2600, 55)),
    "ai_ready": ((1800, 0), (2300, 55), (2800, 55)),
    "warning": ((900, 0), (900, 80)),
    "error": ((600, 0), (600, 80), (600, 80)),
}
_CLICK_MS: Final = 16.0
_CLICK_DECAY_S: Final = 0.0035
_CLICK_AMPLITUDE: Final = 0.6


def cue_label(cue: CueName) -> str:
    """Localized name of a cue, for validation messages."""
    labels: dict[CueName, str] = {
        "start": _("Recording started"),
        "transcribing": _("Transcribing"),
        "ready": _("Copied"),
        "ai_ready": _("Claude answered"),
        "warning": _("Warning"),
        "error": _("Error"),
    }
    return labels[cue]


def cue_gain(master_volume: float, cue_volume: float) -> float:
    return max(0.0, min(1.0, master_volume)) * max(0.0, min(1.0, cue_volume))


def _sine(tone: _Tone, amplitude: float, fade_ms: float) -> list[float]:
    count = int(SAMPLE_RATE * tone.ms / 1000)
    step = 2 * math.pi * tone.freq / SAMPLE_RATE
    out = [amplitude * math.sin(step * i) for i in range(count)]
    fade = int(SAMPLE_RATE * fade_ms / 1000)
    if fade > 0 and count > 2 * fade:
        for i in range(fade):
            ramp = i / fade
            out[i] *= ramp
            out[count - 1 - i] *= ramp
    return out


def _click(freq: float, amplitude: float) -> list[float]:
    count = int(SAMPLE_RATE * _CLICK_MS / 1000)
    step = 2 * math.pi * freq / SAMPLE_RATE
    seed = 0x2545F491  # deterministic noise: the content hash must be stable
    out: list[float] = []
    for i in range(count):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        noise = seed / 0x3FFFFFFF - 1.0
        envelope = math.exp(-(i / SAMPLE_RATE) / _CLICK_DECAY_S)
        out.append(amplitude * envelope * (0.75 * math.sin(step * i) + 0.25 * noise))
    return out


def render_samples(cue: CueName, preset: CuePreset) -> list[float]:
    """Samples in -1..1 at SAMPLE_RATE (mono), before the gain."""
    samples: list[float] = []
    if preset == "click":
        for freq, gap_ms in _CLICK_PATTERNS[cue]:
            samples.extend([0.0] * int(SAMPLE_RATE * gap_ms / 1000))
            samples.extend(_click(freq, _CLICK_AMPLITUDE))
        return samples
    for tone in _CLASSIC_TONES[cue]:
        if preset == "soft":
            longer = _Tone(tone.freq, tone.ms * _SOFT_STRETCH)
            samples.extend(_sine(longer, _SOFT_AMPLITUDE, longer.ms * _SOFT_FADE_SHARE))
        else:
            samples.extend(_sine(tone, _CLASSIC_AMPLITUDE, _CLASSIC_FADE_MS))
    return samples


def _wav_bytes(channels: int, width: int, rate: int, frames: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return buffer.getvalue()


def encode_wav(samples: Sequence[float], gain: float) -> bytes:
    """16-bit mono PCM WAV with `gain` (0..1) applied."""
    factor = max(0.0, min(1.0, gain)) * 32767
    pcm = array("h", (max(-32767, min(32767, round(sample * factor))) for sample in samples))
    if sys.byteorder == "big":
        pcm.byteswap()  # WAV is little-endian
    return _wav_bytes(1, 2, SAMPLE_RATE, pcm.tobytes())


def render_preset(cue: CueName, preset: CuePreset, gain: float) -> bytes:
    return encode_wav(render_samples(cue, preset), gain)


def _read_pcm(path: Path) -> tuple[int, int, int, bytes]:
    try:
        with wave.open(str(path), "rb") as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            rate = reader.getframerate()
            count = reader.getnframes()
            if reader.getcomptype() != "NONE" or width not in (1, 2) or channels not in (1, 2) or rate <= 0:
                raise CueFileError("format", f"{channels} ch, {width * 8} bit, {rate} Hz")
            if count / rate > MAX_CUSTOM_SECONDS:
                raise CueFileError("too_long", f"{count / rate:.1f} s")
            frames = reader.readframes(count)
    except FileNotFoundError as exc:
        raise CueFileError("missing", str(exc)) from exc
    except (wave.Error, EOFError) as exc:
        raise CueFileError("format", str(exc)) from exc
    except OSError as exc:
        raise CueFileError("missing", str(exc)) from exc
    return channels, width, rate, frames


def check_cue_file(path: str) -> CueFileProblem | None:
    """Validation on apply: a PCM 8/16-bit WAV, mono or stereo, at most MAX_CUSTOM_SECONDS."""
    if not path.strip():
        return "missing"
    try:
        _read_pcm(Path(path))
    except CueFileError as exc:
        return exc.problem
    return None


def render_file(path: Path, gain: float) -> bytes:
    """Re-render a custom WAV with the gain baked in (same format as the source)."""
    channels, width, rate, frames = _read_pcm(path)
    factor = max(0.0, min(1.0, gain))
    if width == 1:  # unsigned 8-bit, centered on 128
        scaled = bytes(max(0, min(255, round((value - 128) * factor) + 128)) for value in frames)
        return _wav_bytes(channels, width, rate, scaled)
    pcm = array("h")
    pcm.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":
        pcm.byteswap()
    out = array("h", (max(-32768, min(32767, round(value * factor))) for value in pcm))
    if sys.byteorder == "big":
        out.byteswap()
    return _wav_bytes(channels, width, rate, out.tobytes())


class CueBank:
    """Writes rendered cues under `directory`, named by content hash."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    @property
    def directory(self) -> Path:
        return self._dir

    def _store(self, cue: CueName, data: bytes) -> Path:
        digest = hashlib.sha256(data).hexdigest()[:16]
        path = self._dir / f"{cue}-{digest}.wav"
        if path.is_file():
            return path
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path

    def render(self, cue: CueName, settings: CueSettings, master_volume: float) -> Path:
        """The WAV for this cue; a custom file that became unusable falls back to the preset."""
        gain = cue_gain(master_volume, settings.volume)
        if settings.source == "file":
            try:
                return self._store(cue, render_file(Path(settings.file), gain))
            except CueFileError as exc:
                log.warning(
                    "cue %s: custom file %r unusable (%s); using the %s preset",
                    cue,
                    settings.file,
                    exc,
                    settings.preset,
                )
        return self._store(cue, render_preset(cue, settings.preset, gain))

    def cleanup(self, keep: Collection[Path], min_age_s: float = 60.0) -> None:
        """Delete old renders (best effort: a file still playing may be locked)."""
        keep_names = {path.name for path in keep}
        now = time.time()
        try:
            entries = list(self._dir.glob("*.wav"))
        except OSError:
            return
        for entry in entries:
            if entry.name in keep_names:
                continue
            try:
                if now - entry.stat().st_mtime >= min_age_s:
                    entry.unlink()
            except OSError:
                continue


class CuePlayer:
    """Plays cues by name. `configure` renders (call it off the dispatcher); `play` is cheap."""

    def __init__(self, bank: CueBank, play_file: Callable[[Path], None]) -> None:
        self._bank = bank
        self._play_file = play_file
        self._lock = threading.Lock()
        self._paths: Mapping[CueName, Path] = {}
        self._enabled: Mapping[CueName, bool] = {}
        self._muted = False

    def configure(self, settings: CompanionSettings) -> None:
        paths: dict[CueName, Path] = {}
        for cue in CUE_NAMES:
            cue_settings = settings.cues.get(cue, CueSettings())
            try:
                paths[cue] = self._bank.render(cue, cue_settings, settings.master_volume)
            except OSError as exc:
                log.warning("cue %s could not be rendered: %s", cue, exc)
        with self._lock:
            self._paths = paths
            self._enabled = {cue: settings.cues.get(cue, CueSettings()).enabled for cue in CUE_NAMES}
            self._muted = not settings.cues_enabled
        self._bank.cleanup(paths.values())

    def path(self, cue: CueName) -> Path | None:
        with self._lock:
            return self._paths.get(cue)

    def play(self, cue: CueName) -> bool:
        """Plays the cue unless muted or disabled. True when a sound was started."""
        with self._lock:
            path = self._paths.get(cue)
            allowed = not self._muted and self._enabled.get(cue, True)
        if path is None or not allowed:
            return False
        try:
            self._play_file(path)
        except Exception:  # noqa: BLE001 - a sound must never break the caller
            log.exception("cue %s could not be played", cue)
            return False
        return True

    def preview(self, cue: CueName, settings: CueSettings, master_volume: float) -> None:
        """Render and play one cue with unsaved settings (ignores mute and `enabled`)."""
        try:
            path = self._bank.render(cue, settings, master_volume)
            self._play_file(path)
        except Exception:  # noqa: BLE001 - preview is best effort
            log.exception("cue preview %s failed", cue)
