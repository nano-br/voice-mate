from __future__ import annotations

import queue
import sys
import threading
from typing import Any, Protocol

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray

from app.platform.detect import detect_platform


class AudioSink(Protocol):
    """Interface comum dos players de áudio (sounddevice e paplay)."""

    def ensure_started(self, sample_rate: int) -> None: ...

    def start(self, sample_rate: int) -> None: ...

    def feed(self, chunk: NDArray[np.float32]) -> None: ...

    def drain(self, timeout: float | None = 60.0) -> bool: ...

    def abort(self) -> None: ...

    def close(self) -> None: ...


def create_audio_player() -> AudioSink:
    """Escolhe o player por plataforma.

    Linux/WSL2 com `paplay`: PulseAudio nativo (robusto no WSLg, sem o deadlock
    do PortAudio sobre RDP que pendurava o app). Windows (WASAPI): sounddevice.
    """
    if detect_platform() in ("wsl2", "linux-x11", "linux-wayland"):
        from app.features.tts.paplay_player import PaplayPlayer, paplay_available

        if paplay_available():
            return PaplayPlayer()
    return AudioPlayer()


# Buffer de saída por plataforma. No WSLg o áudio sai por PulseAudio sobre RDP,
# que tem jitter alto: blocos minúsculos (blocksize=0, ~34 ms) esvaziam o buffer
# no meio da fala → underrun → chiado. blocksize=4096 @ 24 kHz (~170 ms) +
# latency=0.2 dão ~340 ms efetivos (medido), folga suficiente p/ o RDP.
# Windows/WASAPI já funciona com o default — não mexer.
_WSL_LINUX_BLOCKSIZE = 4096
_WSL_LINUX_LATENCY = 0.2


def _default_audio_params() -> tuple[int, float | str | None]:
    """(blocksize, latency) conforme a plataforma. WSL2/Linux → buffer maior."""
    if detect_platform() in ("wsl2", "linux-x11", "linux-wayland"):
        return _WSL_LINUX_BLOCKSIZE, _WSL_LINUX_LATENCY
    return 0, None  # Windows/macOS: default do PortAudio


class AudioPlayer:
    """Player de áudio com fila para chunks float32 mono.

    Reproduz em tempo real chunks enviados via `feed()`. `drain()` bloqueia
    até a fila esvaziar; `abort()` interrompe imediatamente (descarta a fila
    e aborta o buffer do driver).
    """

    def __init__(self, blocksize: int | None = None, latency: float | str | None = None) -> None:
        default_blocksize, default_latency = _default_audio_params()
        self._blocksize = default_blocksize if blocksize is None else blocksize
        self._latency = default_latency if latency is None else latency
        self._queue: queue.Queue[NDArray[np.float32]] = queue.Queue()
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self._aborted = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._leftover: NDArray[np.float32] | None = None
        self._sample_rate: int | None = None
        self._underflow_logged = False

    def ensure_started(self, sample_rate: int) -> None:
        """Garante um stream aberto e compatível — abre só se necessário.

        Diferente de `start()`, **não reabre** se já há um stream ativo com o
        mesmo sample_rate. Isso mantém UM stream persistente entre frases/turnos,
        eliminando os cliques de abrir/fechar a cada fala e os buracos entre falas.
        """
        with self._lock:
            ready = self._stream is not None and not self._aborted.is_set() and self._sample_rate == sample_rate
        if ready:
            return
        self.start(sample_rate)

    def start(self, sample_rate: int) -> None:
        """Cria um novo OutputStream — fecha o antigo se existir.

        Cada chamada gera um stream fresco, evitando degradação após muitos
        usos ou após `abort()`. Limpa flags internos para que `feed()` volte
        a aceitar chunks normalmente. Prefira `ensure_started()` no caminho
        normal de fala; use `start()` para forçar um stream novo.
        """
        with self._lock:
            old = self._stream
            self._stream = None
            self._aborted.clear()
            self._idle.set()
            self._leftover = None
            self._underflow_logged = False
            self._drain_queue()
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._blocksize,
                latency=self._latency,
                callback=self._callback,
            )
            stream.start()
            self._stream = stream
            self._sample_rate = sample_rate
        if old is not None:
            self._close_stream_safely(old)

    def feed(self, chunk: NDArray[np.float32]) -> None:
        if self._aborted.is_set():
            return
        if chunk.ndim == 1:
            chunk = chunk.reshape(-1, 1)
        if chunk.dtype != np.float32:
            chunk = chunk.astype(np.float32)
        self._idle.clear()
        self._queue.put(chunk)

    def drain(self, timeout: float | None = 60.0) -> bool:
        """Bloqueia até a fila esvaziar (ou `abort()`). Retorna True se idle.

        Default 60s para evitar travamento eterno caso o callback do
        sounddevice pare de rodar por algum motivo (driver, etc).
        """
        return self._idle.wait(timeout=timeout)

    def abort(self) -> None:
        """Interrompe imediatamente, fechando o stream e limpando o estado.

        Após `abort()`, o player volta ao estado inicial — uma chamada a
        `start()` cria um stream novo e `feed()` volta a funcionar.
        """
        self._aborted.set()
        self._drain_queue()
        self._leftover = None
        with self._lock:
            stream = self._stream
            self._stream = None
            self._sample_rate = None
        if stream is not None:
            try:
                stream.abort()
            except Exception as exc:  # noqa: BLE001
                print(f"[AudioPlayer] abort falhou: {exc}", file=sys.stderr)
            self._close_stream_safely(stream)
        self._idle.set()

    def close(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            stream = self._stream
            self._stream = None
            self._sample_rate = None
        self._close_stream_safely(stream)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    @staticmethod
    def _close_stream_safely(stream: sd.OutputStream) -> None:
        try:
            stream.stop()
            stream.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[AudioPlayer] close falhou: {exc}", file=sys.stderr)

    def _callback(
        self,
        outdata: NDArray[np.float32],
        frames: int,
        time_info: Any,  # noqa: ANN401
        status: sd.CallbackFlags,
    ) -> None:
        if status and not self._underflow_logged:
            # Loga só a 1ª ocorrência por stream — antes floodava o console a cada
            # callback. Um underflow isolado no início (fila ainda enchendo) é normal.
            print(f"[AudioPlayer] {status}", file=sys.stderr)
            self._underflow_logged = True
        if self._aborted.is_set():
            outdata.fill(0)
            return
        filled = 0
        if self._leftover is not None:
            take = min(frames - filled, self._leftover.shape[0])
            outdata[filled : filled + take, 0] = self._leftover[:take, 0]
            if take == self._leftover.shape[0]:
                self._leftover = None
            else:
                self._leftover = self._leftover[take:]
            filled += take
        while filled < frames:
            try:
                chunk = self._queue.get_nowait()
            except queue.Empty:
                outdata[filled:].fill(0)
                self._idle.set()
                return
            take = min(frames - filled, chunk.shape[0])
            outdata[filled : filled + take, 0] = chunk[:take, 0]
            if take < chunk.shape[0]:
                self._leftover = chunk[take:]
            filled += take
