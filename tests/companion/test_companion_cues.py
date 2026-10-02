from __future__ import annotations

import io
import os
import time
import wave
from array import array
from pathlib import Path

import pytest

from app.companion.contract import CUE_NAMES, CompanionSettings, CueName, CuePreset, CueSettings
from app.companion.cues import (
    SAMPLE_RATE,
    CueBank,
    CueFileError,
    CuePlayer,
    check_cue_file,
    cue_gain,
    render_file,
    render_preset,
    render_samples,
)


def _read(data: bytes) -> tuple[int, int, int, bytes]:
    with wave.open(io.BytesIO(data), "rb") as reader:
        return (
            reader.getnchannels(),
            reader.getsampwidth(),
            reader.getframerate(),
            reader.readframes(reader.getnframes()),
        )


def _peak(frames: bytes) -> int:
    samples = array("h")
    samples.frombytes(frames)
    return max(abs(sample) for sample in samples)


@pytest.mark.parametrize("preset", ["classic", "soft", "click"])
@pytest.mark.parametrize("cue", CUE_NAMES)
def test_every_preset_renders_a_16_bit_mono_wav(cue: CueName, preset: CuePreset) -> None:
    channels, width, rate, frames = _read(render_preset(cue, preset, 1.0))
    assert (channels, width, rate) == (1, 2, SAMPLE_RATE)
    assert len(frames) > 0
    assert 0 < _peak(frames) <= 32767


def test_classic_matches_the_engine_tone_lengths() -> None:
    expected_ms = {"start": 120, "transcribing": 70, "ready": 250, "ai_ready": 360, "warning": 200, "error": 300}
    for cue, ms in expected_ms.items():
        assert len(render_samples(cue, "classic")) == pytest.approx(SAMPLE_RATE * ms / 1000, abs=3)  # type: ignore[arg-type]


def test_soft_is_quieter_and_longer_than_classic() -> None:
    classic = render_samples("start", "classic")
    soft = render_samples("start", "soft")
    assert len(soft) > len(classic)
    assert max(map(abs, soft)) < max(map(abs, classic))


def test_gain_is_baked_in_and_rendering_is_deterministic() -> None:
    loud = render_preset("ready", "classic", 1.0)
    quiet = render_preset("ready", "classic", 0.25)
    assert render_preset("ready", "classic", 1.0) == loud
    assert _peak(_read(quiet)[3]) == pytest.approx(_peak(_read(loud)[3]) / 4, rel=0.01)
    assert _peak(_read(render_preset("error", "click", 0.0))[3]) == 0
    assert render_preset("start", "click", 0.5) == render_preset("start", "click", 0.5)


def test_cue_gain_clamps() -> None:
    assert cue_gain(0.8, 0.5) == pytest.approx(0.4)
    assert cue_gain(2.0, -1.0) == 0.0
    assert cue_gain(1.5, 1.5) == 1.0


def _write_wav(path: Path, width: int, samples: list[int], channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(8000)
        if width == 1:
            writer.writeframes(bytes(samples))
        else:
            writer.writeframes(array("h", samples).tobytes())
    return path


def test_custom_16_bit_file_is_rerendered_with_the_gain(tmp_path: Path) -> None:
    source = _write_wav(tmp_path / "in.wav", 2, [0, 10000, -20000, 32767], channels=2)
    channels, width, rate, frames = _read(render_file(source, 0.5))
    assert (channels, width, rate) == (2, 2, 8000)
    out = array("h")
    out.frombytes(frames)
    assert list(out) == [0, 5000, -10000, 16384]


def test_custom_8_bit_file_is_scaled_around_its_center(tmp_path: Path) -> None:
    source = _write_wav(tmp_path / "in8.wav", 1, [128, 255, 0, 192])
    _channels, width, _rate, frames = _read(render_file(source, 0.5))
    assert width == 1
    assert list(frames) == [128, 192, 64, 160]


def test_custom_file_problems(tmp_path: Path) -> None:
    assert check_cue_file(str(tmp_path / "nope.wav")) == "missing"
    assert check_cue_file("") == "missing"
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"RIFF....WAVEfmt ")
    assert check_cue_file(str(junk)) == "format"
    with pytest.raises(CueFileError) as raised:
        render_file(junk, 1.0)
    assert raised.value.problem == "format"


def test_bank_names_files_by_content_hash(tmp_path: Path) -> None:
    bank = CueBank(tmp_path / "cues")
    first = bank.render("start", CueSettings(), 0.8)
    again = bank.render("start", CueSettings(), 0.8)
    other = bank.render("start", CueSettings(volume=0.5), 0.8)
    assert first == again
    assert other != first
    assert first.name.startswith("start-") and first.suffix == ".wav"
    assert first.is_file() and other.is_file()


def test_bank_falls_back_to_the_preset_when_the_custom_file_vanished(tmp_path: Path) -> None:
    bank = CueBank(tmp_path)
    path = bank.render("error", CueSettings(source="file", file=str(tmp_path / "gone.wav"), preset="click"), 1.0)
    assert path.read_bytes() == render_preset("error", "click", 1.0)


def test_cleanup_keeps_current_and_recent_files(tmp_path: Path) -> None:
    bank = CueBank(tmp_path)
    keep = bank.render("start", CueSettings(), 1.0)
    old = bank.render("start", CueSettings(volume=0.1), 1.0)
    fresh = bank.render("start", CueSettings(volume=0.2), 1.0)
    an_hour_ago = time.time() - 3600
    os.utime(old, (an_hour_ago, an_hour_ago))
    bank.cleanup([keep])
    assert keep.is_file()
    assert not old.exists()
    assert fresh.is_file()  # may still be playing


def test_player_respects_mute_and_per_cue_enabled(tmp_path: Path) -> None:
    played: list[Path] = []
    player = CuePlayer(CueBank(tmp_path), played.append)
    cues = {name: CueSettings() for name in CUE_NAMES}
    cues["warning"] = CueSettings(enabled=False)
    player.configure(CompanionSettings(cues=cues))
    assert player.play("start") is True
    assert player.play("warning") is False
    player.configure(CompanionSettings(cues=cues, cues_enabled=False))
    assert player.play("start") is False
    assert len(played) == 1
    player.preview("warning", CueSettings(preset="click"), 0.5)  # previews ignore mute
    assert len(played) == 2
    assert played[-1].read_bytes() == render_preset("warning", "click", 0.5)
