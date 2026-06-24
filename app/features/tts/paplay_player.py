"""Player de áudio via `paplay` (PulseAudio) — robusto no WSLg.

Por que não o sounddevice/PortAudio aqui: no WSL2 a saída de áudio é PulseAudio
sobre RDP (WSLg), e o stream de callback do PortAudio é frágil nesse transporte —
os underruns desestabilizam o stream e uma operação de stop/close pode TRAVAR
(`paTimedOut`), pendurando o app inteiro (nem Ctrl+C mata). O `paplay` lê PCM cru
do stdin e deixa o próprio PulseAudio cuidar do buffer (nativo, sem callback),
então não há deadlock e o processo é sempre matável.

Mesma interface do `AudioPlayer` (ensure_started/feed/drain/abort/close). Uma
thread escritora consome a fila e escreve no stdin do `paplay` — assim `feed()`
não bloqueia (mantém o pipeline: sintetizar a próxima frase enquanto a atual
toca). Todos os waits têm timeout: o app nunca pendura no encerramento.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading

import numpy as np
from numpy.typing import NDArray

_SENTINEL = None


def paplay_available() -> bool:
    return shutil.which("paplay") is not None


class PaplayPlayer:
    """Reproduz chunks float32 mono enviando PCM cru para o `paplay`."""

    def __init__(self) -> None:
        self._queue: queue.Queue[NDArray[np.float32] | None] = queue.Queue()
        self._proc: subprocess.Popen[bytes] | None = None
        self._writer: threading.Thread | None = None
        self._sample_rate: int | None = None
        self._lock = threading.Lock()

    def ensure_started(self, sample_rate: int) -> None:
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None
            if alive and self._sample_rate == sample_rate:
                return
        self.start(sample_rate)

    def start(self, sample_rate: int) -> None:
        self._stop_process()
        proc = subprocess.Popen(  # noqa: S603
            ["paplay", "--raw", "--format=float32le", f"--rate={sample_rate}", "--channels=1"],  # noqa: S607
            stdin=subprocess.PIPE,
        )
        with self._lock:
            self._drain_queue()
            self._proc = proc
            self._sample_rate = sample_rate
            self._writer = threading.Thread(target=self._write_loop, args=(proc,), daemon=True, name="Paplay")
            self._writer.start()

    def feed(self, chunk: NDArray[np.float32]) -> None:
        self._queue.put(np.ascontiguousarray(chunk, dtype=np.float32).reshape(-1))

    def drain(self, timeout: float | None = 60.0) -> bool:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._sample_rate = None
        if proc is None:
            return True
        self._queue.put(_SENTINEL)  # writer fecha o stdin após esvaziar a fila
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            self._kill(proc)
            return False

    def abort(self) -> None:
        self._stop_process()

    def close(self) -> None:
        self._stop_process()

    # ── internos ──────────────────────────────────────────────────────────────

    def _write_loop(self, proc: subprocess.Popen[bytes]) -> None:
        stdin = proc.stdin
        if stdin is None:
            return
        while True:
            chunk = self._queue.get()
            if chunk is _SENTINEL:
                break
            try:
                stdin.write(chunk.tobytes())
                stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return  # processo morto (abort) — para de escrever
        try:
            stdin.close()  # EOF → paplay drena e sai
        except OSError:
            pass

    def _stop_process(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._sample_rate = None
            self._drain_queue()
        self._queue.put(_SENTINEL)  # destrava o writer se estiver bloqueado no get
        if proc is None:
            return
        self._kill(proc)

    @staticmethod
    def _kill(proc: subprocess.Popen[bytes]) -> None:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        except OSError as exc:
            print(f"[PaplayPlayer] falha ao encerrar paplay: {exc}", file=sys.stderr)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
