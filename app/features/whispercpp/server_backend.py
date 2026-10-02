"""Transcription via whisper-server.exe (whisper.cpp + Vulkan) kept warm.

Starts `whisper-server.exe` once (model loaded in VRAM) and does `POST /inference`
per utterance — eliminating the model reload from disk that the CLI backend
(`WhisperCppBackend`) pays on every transcription, which is the biggest latency
bottleneck of the push-to-talk flow.

Uses only the stdlib (`urllib` + manual multipart) — no new dependency. Satisfies
the `core.transcription_backend.TranscriptionBackend` Protocol and exposes
`close()` to shut down the subprocess on shutdown (called by `main.py`).
"""

from __future__ import annotations

import io
import json
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import wave
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from app.core.config import Config
from app.i18n import _

_READY_TIMEOUT_SECONDS = 120.0
_INFER_TIMEOUT_SECONDS = 120.0
_WARMUP_SECONDS = 0.3


def _free_port() -> int:
    """Reserve a free port on loopback (race-y, but enough for local use)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _audio_to_wav_bytes(audio: NDArray[np.float32], sample_rate: int) -> bytes:
    """Convert float32 [-1,1] → mono PCM16 WAV in memory (the format the server reads)."""
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _build_multipart(fields: dict[str, str], filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    """Build a multipart/form-data body with the fields + the `file` part."""
    boundary = f"----voicemate{uuid.uuid4().hex}"
    sep = boundary.encode()
    crlf = b"\r\n"
    body = bytearray()
    for name, value in fields.items():
        body += b"--" + sep + crlf
        body += f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
        body += value.encode() + crlf
    body += b"--" + sep + crlf
    body += f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode() + crlf
    body += b"Content-Type: audio/wav" + crlf + crlf
    body += file_bytes + crlf
    body += b"--" + sep + b"--" + crlf
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _vulkan_device_warning(log_text: str) -> str | None:
    """Warning when whisper-server's Vulkan fell back to a software device.

    On WSL2, Mesa only exposes llvmpipe (CPU) — the real GPU is reachable only via
    ROCm. Detecting this at boot avoids the "transcription takes minutes" symptom.
    """
    for line in log_text.splitlines():
        low = line.lower()
        if "ggml_vulkan" not in low and "found device" not in low and "devices:" not in low:
            continue
        if "llvmpipe" in low or "dozen" in low or "(cpu)" in low:
            return (
                f"Vulkan WITHOUT a real GPU ({line.strip()}) — transcription will be VERY slow. "
                "On WSL2 the AMD GPU only accelerates via ROCm: use openai-whisper or CT2-ROCm "
                "(run `make configure`)."
            )
    return None


def _parse_response(raw: str) -> str:
    """Extract the text from the server's `{"text": ...}` JSON.

    The ``text`` field already comes with the segments faithfully concatenated by
    whisper.cpp itself — never re-segment/re-join by line here: the space re-join
    was the cause of cut words ("pa lavra").
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unexpected (non-JSON) response from whisper-server: {raw[:200]!r}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected response from whisper-server: {raw[:200]!r}")
    if "error" in data:
        raise RuntimeError(f"whisper-server returned an error: {data['error']}")
    if "text" not in data:
        raise RuntimeError(f"whisper-server response missing 'text' field: {raw[:200]!r}")
    return str(data["text"])


class WhisperCppServerBackend:
    """Transcription backend with a warm model via whisper-server.exe."""

    def __init__(self, config: Config, exe: Path, model: Path) -> None:
        self._exe = exe
        self._model = model
        self._beam = config.beam_size
        self._sample_rate = config.sample_rate
        self._language = config.transcription_language
        self._port = _free_port()
        self._base_url = f"http://127.0.0.1:{self._port}"
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_path: Path = exe.parent / "server.log"
        self._start()

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        if self._proc is not None and self._proc.poll() is not None:
            raise RuntimeError(f"whisper-server died (code {self._proc.returncode}).")
        return self._infer(audio).strip()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _start(self) -> None:
        cmd = [
            str(self._exe),
            "-m",
            str(self._model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self._port),
            "-bs",
            str(self._beam),
            "-l",
            self._language,  # "auto" or ISO 639-1 code (pt, en, ...)
        ]
        # VAD (silero): trims by silence instead of fixed time — avoids cutting
        # mid-word. Opt-in: only when the VAD model was downloaded by setup into
        # the same directory.
        from app.features.whispercpp import find_vad_model

        vad_model = find_vad_model(self._model.parent)
        if vad_model is not None:
            cmd += ["--vad", "--vad-model", str(vad_model)]
        print(
            _(
                "[VoiceMate] Starting whisper-server '{model_stem}' (whisper.cpp, language={language}, vad={vad})..."
            ).format(
                model_stem=self._model.stem,
                language=self._language,
                vad="on" if vad_model else "off",
            )
        )
        # stdout/stderr go to a log file (not DEVNULL): it's where ggml prints
        # which Vulkan device was chosen — essential to detect llvmpipe (software
        # Vulkan) on WSL2.
        log_file = self._log_path.open("wb")
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(self._exe.parent),
            )
        finally:
            log_file.close()  # the child inherits the fd; our handle can close
        self._wait_ready()
        self._report_vulkan_device()
        self._warmup()
        print(_("[VoiceMate] Model ready (warm)."))

    def _report_vulkan_device(self) -> None:
        """Log the chosen Vulkan device and warn if it's software (llvmpipe)."""
        try:
            log_text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in log_text.splitlines():
            if "ggml_vulkan" in line.lower() and "device" in line.lower():
                print(_("[VoiceMate] {line}").format(line=line.strip()))
                break
        warning = _vulkan_device_warning(log_text)
        if warning:
            print(_("[VoiceMate] ⚠ {warning}").format(warning=warning), file=sys.stderr)

    def _wait_ready(self, timeout: float = _READY_TIMEOUT_SECONDS) -> None:
        """Wait for the server to accept connections (model already loaded at that point)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(f"whisper-server exited with code {self._proc.returncode} on startup.")
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=1.0):
                    return
            except OSError:
                time.sleep(0.2)
        raise TimeoutError(f"whisper-server did not respond within {timeout:.0f}s.")

    def _warmup(self) -> None:
        """Silence inference to compile Vulkan shaders outside the 1st real turn."""
        silence = np.zeros(int(self._sample_rate * _WARMUP_SECONDS), dtype=np.float32)
        try:
            self._infer(silence)
        except Exception as exc:  # noqa: BLE001
            print(
                _("[VoiceMate] ⚠ whisper-server warmup failed (continuing): {exc}").format(exc=exc),
                file=sys.stderr,
            )

    def _infer(self, audio: NDArray[np.float32]) -> str:
        wav_bytes = _audio_to_wav_bytes(audio, self._sample_rate)
        fields = {"temperature": "0.0", "response_format": "json", "language": self._language}
        body, content_type = _build_multipart(fields, "audio.wav", wav_bytes)
        req = urllib.request.Request(  # noqa: S310 — fixed loopback URL
            f"{self._base_url}/inference",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_INFER_TIMEOUT_SECONDS) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
        return _parse_response(raw)
