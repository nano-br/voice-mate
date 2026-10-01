import contextlib
import functools
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np
import sounddevice as sd
from numpy.typing import NDArray

from app.i18n import _

# Receives a short, human-readable reason ("PortAudio error ...", "timed out ...").
MicFailureCallback = Callable[[str], None]
# Called once the capture stream is live (off the caller's thread).
MicOpenedCallback = Callable[[], None]

# A healthy open takes well under a second (WSLg included). Past this, the audio
# server is considered stuck: on WSL2 a dead WSLg PulseAudio makes the open hang
# for tens of seconds (or forever) instead of failing.
DEFAULT_OPEN_TIMEOUT = 6.0


def _describe(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= 200 else text[:199] + "…"


class Recorder:
    """Capture microphone audio in toggle mode (start/stop).

    The capture stream is opened OFF the caller's thread: `sd.InputStream().start()`
    can block for a long time when the mic can't be opened (no microphone, or the
    WSLg PulseAudio bridge is dead). Opening it inline stalled the session lock and,
    via the socket trigger, the HTTP response, so the Windows side reported "Daemon
    offline". `start()` therefore reports success optimistically; an open that fails
    or exceeds `open_timeout` reverts to not-recording and is reported through the
    `on_failure` callback, always from another thread (never re-entrant into the caller).
    `on_opened` fires once the stream is live, so a "recording" cue really means the
    mic is capturing.
    """

    def __init__(self, sample_rate: int, open_timeout: float = DEFAULT_OPEN_TIMEOUT) -> None:
        self._sample_rate = sample_rate
        self._open_timeout = open_timeout
        self._recording = False
        self._chunks: list[NDArray[np.float32]] = []
        self._lock = threading.Lock()
        # PortAudio is not thread-safe for opening streams: one open at a time. Never
        # held around stop()/close(), so a hung open can't freeze the stop path.
        self._open_lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        # Each start() opens a new generation: a late open from an older one never
        # attaches to a newer recording.
        self._generation = 0
        # Opens still in flight: generation -> time.monotonic() when it began. Only an
        # open older than `open_timeout` means the audio server is stuck; a young one
        # is just a quick start/stop/start.
        self._inflight: dict[int, float] = {}
        self._reported_generation = 0  # last generation whose failure was reported

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(
        self,
        on_failure: MicFailureCallback | None = None,
        on_opened: MicOpenedCallback | None = None,
    ) -> bool:
        """Begin recording. Returns False if already recording.

        Microphone failures are never raised here: they arrive later via `on_failure`.
        """
        with self._lock:
            if self._recording:
                return False
            self._recording = True
            self._chunks = []
            self._stream = None
            self._generation += 1
            generation = self._generation
            now = time.monotonic()
            stuck = any(now - began >= self._open_timeout for began in self._inflight.values())
            if not stuck:
                self._inflight[generation] = now
        if stuck:
            # An earlier open never returned: the audio server is hung. Stacking
            # another open on top won't help: fail fast with that diagnosis.
            reason = _(
                "the audio system is not responding (a previous microphone open is still stuck; "
                "on WSL2 run `wsl --shutdown`)"
            )
            threading.Thread(target=self._fail, args=(generation, on_failure, reason), daemon=True).start()
            return True
        threading.Thread(
            target=self._open_stream, args=(generation, on_failure, on_opened), daemon=True, name="MicOpen"
        ).start()
        guard = threading.Timer(self._open_timeout, self._open_timed_out, args=(generation, on_failure))
        guard.daemon = True
        guard.start()
        return True

    def _open_stream(
        self, generation: int, on_failure: MicFailureCallback | None, on_opened: MicOpenedCallback | None
    ) -> None:
        """Open the capture stream (runs off-thread; may block/fail on a dead mic)."""
        stream: sd.InputStream | None = None
        try:
            with self._open_lock:
                stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=1,
                    dtype="float32",
                    # Bound to its generation: a stale stream's audio never lands in a newer recording.
                    callback=functools.partial(self._callback, generation),
                )
                stream.start()
        except Exception as exc:  # noqa: BLE001 (mic unavailable: report, clean state)
            with self._lock:
                self._inflight.pop(generation, None)
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()  # created but failed to start: release the handle
            self._fail(generation, on_failure, _describe(exc))
            return
        with self._lock:
            self._inflight.pop(generation, None)
            attach = self._recording and self._generation == generation
            if attach:
                self._stream = stream
        if not attach:
            # stop() already ran, or the open timed out and was reported: discard it.
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()
            return
        if on_opened is not None:
            on_opened()

    def _open_timed_out(self, generation: int, on_failure: MicFailureCallback | None) -> None:
        with self._lock:
            # Still opening (not attached, failed or discarded), even if the user already
            # pressed stop: a hung mic must be reported either way.
            pending = self._generation == generation and generation in self._inflight
            if not pending or not self._claim_failure_locked(generation):
                return
        reason = _("timed out opening the microphone after {seconds:.0f}s").format(seconds=self._open_timeout)
        self._notify(on_failure, reason)

    def _fail(self, generation: int, on_failure: MicFailureCallback | None, reason: str) -> None:
        with self._lock:
            if not self._claim_failure_locked(generation):
                return
        self._notify(on_failure, reason)

    def _claim_failure_locked(self, generation: int) -> bool:
        """Mark `generation` as failed (lock held). False = nothing to report.

        Deciding and reverting atomically matters: the timeout guard and the open
        race, and a guard that reported after the stream attached left the recorder
        capturing forever while the session believed it was idle.
        """
        if self._reported_generation >= generation:
            return False  # already reported (e.g. timed out, then the hung open finally raised)
        if self._generation == generation:
            if self._stream is not None:
                return False  # the open won the race against the guard: nothing failed
            self._recording = False
        self._reported_generation = generation
        return True

    @staticmethod
    def _notify(on_failure: MicFailureCallback | None, reason: str) -> None:
        if on_failure is not None:
            on_failure(reason)
        else:
            print(_("[VoiceMate] ❌ Could not open the microphone ({reason}).").format(reason=reason), file=sys.stderr)

    def stop(self) -> NDArray[np.float32] | None:
        """Stop recording and return the captured audio, or None if empty."""
        with self._lock:
            if not self._recording:
                return None
            self._recording = False
            stream = self._stream
            self._stream = None

        # stream.stop() OUTSIDE the lock: PortAudio blocks until the in-flight
        # callback returns, and _callback needs the same lock to append the
        # chunk — holding it here deadlocked the toggle (visible on WSLg, where
        # the PulseAudio-RDP callbacks are slow/frequent).
        if stream is not None:
            stream.stop()
            stream.close()

        with self._lock:
            chunks = list(self._chunks)
            self._chunks = []

        if not chunks:
            return None

        return np.concatenate(chunks).flatten().astype(np.float32)

    def _callback(
        self,
        generation: int,
        indata: NDArray[np.float32],
        frames: int,  # noqa: ANN001
        time_info: Any,  # noqa: ANN401
        status: sd.CallbackFlags,
    ) -> None:
        """sounddevice callback — invoked automatically for each chunk."""
        if status:
            print(f"[recorder] {status}", file=sys.stderr)
        with self._lock:
            if generation == self._generation:
                self._chunks.append(indata.copy())
