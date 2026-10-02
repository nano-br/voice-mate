"""Avaliação A/B de qualidade dos backends STT contra amostras com referência.

Gate objetivo de qualidade da migração AMD/Linux: garante WER ≤ baseline da
main (faster-whisper CUDA) e zero palavras quebradas ("pa lavra") antes de
aceitar um backend novo.

Formato das amostras (default `samples/ptbr/`): pares `<nome>.wav` (PCM mono,
qualquer sample rate — é reamostrado p/ 16 kHz) + `<nome>.ref.txt` (transcrição
de referência). Uso:

    make stt-eval                                   # avalia todos os backends disponíveis
    make stt-eval ARGS="--backends faster-whisper --save-baseline"   # grava baseline (rodar na main/NVIDIA)
    make stt-eval ARGS="--backends whispercpp"      # compara contra o baseline salvo
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config, TranscriptionLanguage, WhisperBackend
from app.core.transcriber import FasterWhisperBackend
from app.core.transcription_backend import TranscriptionBackend
from app.features import openai_whisper as openai_whisper_feature
from app.features import whispercpp as whispercpp_feature

_ALL_BACKENDS: tuple[WhisperBackend, ...] = ("faster-whisper", "whispercpp", "openai-whisper")
_TARGET_SAMPLE_RATE = 16000
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


# ─── Áudio ───────────────────────────────────────────────────────────────────


def load_audio(path: Path) -> NDArray[np.float32]:
    """Lê um WAV PCM16 → float32 [-1,1] mono 16 kHz (reamostra se preciso)."""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"{path.name}: só WAV PCM16 é suportado (sampwidth={width}).")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != _TARGET_SAMPLE_RATE:
        duration = audio.shape[0] / rate
        target_len = int(duration * _TARGET_SAMPLE_RATE)
        positions = np.linspace(0.0, audio.shape[0] - 1, target_len)
        audio = np.interp(positions, np.arange(audio.shape[0]), audio).astype(np.float32)
    return audio.astype(np.float32)


# ─── Métricas ────────────────────────────────────────────────────────────────


def normalize_tokens(text: str) -> list[str]:
    """Tokens comparáveis: minúsculas, sem pontuação, acentos preservados."""
    text = unicodedata.normalize("NFC", text)
    text = _PUNCT_RE.sub(" ", text.lower())
    return text.split()


def word_error_rate(ref: list[str], hyp: list[str]) -> float:
    """WER clássico via distância de Levenshtein entre listas de palavras."""
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, start=1):
            cost = 0 if ref_word == hyp_word else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[-1] / len(ref)


def count_split_words(ref: list[str], hyp: list[str]) -> int:
    """Conta palavras da referência que aparecem quebradas em duas na hipótese.

    Heurística do sintoma observado no whisper.cpp ("pa lavra"): um par de
    tokens adjacentes da hipótese, ausentes da referência, cuja concatenação
    é uma palavra da referência.
    """
    ref_set = set(ref)
    splits = 0
    for first, second in zip(hyp, hyp[1:], strict=False):
        if first in ref_set or second in ref_set:
            continue
        if first + second in ref_set:
            splits += 1
    return splits


# ─── Backends ────────────────────────────────────────────────────────────────


def build_backend(name: WhisperBackend, config: Config) -> TranscriptionBackend | None:
    if name == "faster-whisper":
        return FasterWhisperBackend(model_size=config.model_size, use_cpu=config.use_cpu, beam_size=config.beam_size)
    if name == "whispercpp":
        if not whispercpp_feature.is_available(config):
            print("  [skip] whispercpp indisponível (rode `make configure`).", file=sys.stderr)
            return None
        return whispercpp_feature.build_backend(config)
    if not openai_whisper_feature.is_available():
        print("  [skip] openai-whisper não instalado (extra whisper-gpu).", file=sys.stderr)
        return None
    return openai_whisper_feature.build_backend(config)


# ─── Avaliação ───────────────────────────────────────────────────────────────


@dataclass
class SampleResult:
    name: str
    wer: float
    split_words: int
    seconds: float


@dataclass
class BackendReport:
    backend: str
    init_seconds: float
    samples: list[SampleResult]

    @property
    def mean_wer(self) -> float:
        return sum(s.wer for s in self.samples) / len(self.samples) if self.samples else 0.0

    @property
    def total_splits(self) -> int:
        return sum(s.split_words for s in self.samples)

    @property
    def mean_seconds(self) -> float:
        return sum(s.seconds for s in self.samples) / len(self.samples) if self.samples else 0.0


def find_samples(samples_dir: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for wav_path in sorted(samples_dir.glob("*.wav")):
        ref_path = wav_path.with_suffix("").with_suffix(".ref.txt")
        if not ref_path.exists():
            ref_path = wav_path.with_name(wav_path.stem + ".ref.txt")
        if ref_path.exists():
            pairs.append((wav_path, ref_path))
        else:
            print(f"  [skip] {wav_path.name}: sem {wav_path.stem}.ref.txt", file=sys.stderr)
    return pairs


def evaluate_backend(name: WhisperBackend, config: Config, pairs: list[tuple[Path, Path]]) -> BackendReport | None:
    print(f"\n→ Backend: {name}")
    start = time.perf_counter()
    try:
        backend = build_backend(name, config)
    except Exception as exc:  # noqa: BLE001 — eval segue p/ os demais backends
        print(f"  [erro] init falhou: {exc}", file=sys.stderr)
        return None
    if backend is None:
        return None
    init_seconds = time.perf_counter() - start
    print(f"  init: {init_seconds:.1f}s")

    results: list[SampleResult] = []
    try:
        for wav_path, ref_path in pairs:
            audio = load_audio(wav_path)
            ref_tokens = normalize_tokens(ref_path.read_text(encoding="utf-8"))
            t0 = time.perf_counter()
            hyp_text = backend.transcribe(audio)
            elapsed = time.perf_counter() - t0
            hyp_tokens = normalize_tokens(hyp_text)
            result = SampleResult(
                name=wav_path.stem,
                wer=word_error_rate(ref_tokens, hyp_tokens),
                split_words=count_split_words(ref_tokens, hyp_tokens),
                seconds=elapsed,
            )
            results.append(result)
            flag = f"  ⚠ {result.split_words} palavra(s) quebrada(s)!" if result.split_words else ""
            print(f"  {result.name}: WER {result.wer:.1%} em {elapsed:.1f}s{flag}")
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            close()
    return BackendReport(backend=name, init_seconds=init_seconds, samples=results)


# ─── Baseline ────────────────────────────────────────────────────────────────


def baseline_path(samples_dir: Path) -> Path:
    return samples_dir / "baseline.json"


def save_baseline(report: BackendReport, samples_dir: Path) -> None:
    payload = {
        "backend": report.backend,
        "mean_wer": report.mean_wer,
        "total_splits": report.total_splits,
        "mean_seconds": report.mean_seconds,
        "init_seconds": report.init_seconds,
        "samples": {s.name: s.wer for s in report.samples},
    }
    path = baseline_path(samples_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nBaseline gravado em {path} (backend {report.backend}).")


def load_baseline(samples_dir: Path) -> dict[str, object] | None:
    path = baseline_path(samples_dir)
    if not path.exists():
        return None
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return data


# ─── Relatório ───────────────────────────────────────────────────────────────


def print_summary(reports: list[BackendReport], baseline: dict[str, object] | None) -> None:
    print("\n" + "=" * 72)
    header = f"{'backend':<18} {'WER médio':>10} {'quebradas':>10} {'s/amostra':>10} {'init (s)':>9}"
    print(header)
    print("-" * 72)
    for report in reports:
        print(
            f"{report.backend:<18} {report.mean_wer:>9.1%} {report.total_splits:>10d} "
            f"{report.mean_seconds:>10.1f} {report.init_seconds:>9.1f}"
        )
    if baseline is not None:
        ref_backend = str(baseline.get("backend", "?"))
        ref_wer = float(str(baseline.get("mean_wer", 0.0)))
        print("-" * 72)
        print(f"{'baseline (' + ref_backend + ')':<18} {ref_wer:>9.1%}")
        for report in reports:
            delta = report.mean_wer - ref_wer
            verdict = "OK (≤ baseline + 1 p.p.)" if delta <= 0.01 else "ACIMA do baseline + 1 p.p. ✗"
            print(f"  {report.backend}: ΔWER {delta:+.1%} → {verdict}")
    print("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Avalia backends STT contra amostras com referência.")
    parser.add_argument("--samples", default="samples/ptbr", help="diretório com pares .wav/.ref.txt")
    parser.add_argument(
        "--backends",
        nargs="+",
        choices=list(_ALL_BACKENDS),
        default=list(_ALL_BACKENDS),
        help="backends a avaliar (default: todos os disponíveis)",
    )
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--language", default="pt", help="idioma fixado na transcrição")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--cpu", action="store_true", help="força CPU (faster-whisper)")
    parser.add_argument(
        "--save-baseline",
        action="store_true",
        help="grava o resultado do primeiro backend avaliado como baseline (rodar na main/NVIDIA)",
    )
    args = parser.parse_args(argv)

    samples_dir = Path(args.samples)
    if not samples_dir.is_dir():
        print(f"Diretório de amostras não existe: {samples_dir}", file=sys.stderr)
        print("Crie-o com pares <nome>.wav + <nome>.ref.txt (veja samples/ptbr/README.md).", file=sys.stderr)
        return 2
    pairs = find_samples(samples_dir)
    if not pairs:
        print(f"Nenhum par .wav/.ref.txt em {samples_dir}.", file=sys.stderr)
        return 2
    print(f"{len(pairs)} amostra(s) em {samples_dir}.")

    language: TranscriptionLanguage = args.language
    config = Config(
        model_size=args.model,
        use_cpu=args.cpu,
        beam_size=args.beam_size,
        transcription_language=language,
    )

    reports: list[BackendReport] = []
    for name in args.backends:
        report = evaluate_backend(name, config, pairs)
        if report is not None:
            reports.append(report)

    if not reports:
        print("Nenhum backend pôde ser avaliado.", file=sys.stderr)
        return 1

    if args.save_baseline:
        save_baseline(reports[0], samples_dir)

    print_summary(reports, load_baseline(samples_dir))
    has_splits = any(r.total_splits for r in reports)
    return 1 if has_splits else 0


if __name__ == "__main__":
    sys.exit(main())
