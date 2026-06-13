"""Transcrição via whisper-server.exe (whisper.cpp + Vulkan) mantido quente.

Sobe o `whisper-server.exe` uma única vez (modelo carregado na VRAM) e faz
`POST /inference` por fala — elimina a recarga do modelo do disco que o backend
CLI (`WhisperCppBackend`) paga a cada transcrição, que é o maior gargalo de
latência do fluxo push-to-talk.

Usa só a stdlib (`urllib` + multipart manual) — sem nova dependência. Satisfaz o
Protocol `core.transcription_backend.TranscriptionBackend` e expõe `close()` para
encerrar o subprocess no shutdown (chamado por `main.py`).
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

_READY_TIMEOUT_SECONDS = 120.0
_INFER_TIMEOUT_SECONDS = 120.0
_WARMUP_SECONDS = 0.3


def _free_port() -> int:
    """Reserva uma porta livre em loopback (race-y, mas suficiente p/ uso local)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _audio_to_wav_bytes(audio: NDArray[np.float32], sample_rate: int) -> bytes:
    """Converte float32 [-1,1] → WAV PCM16 mono em memória (formato que o server lê)."""
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm16.tobytes())
    return buf.getvalue()


def _build_multipart(fields: dict[str, str], filename: str, file_bytes: bytes) -> tuple[bytes, str]:
    """Monta um corpo multipart/form-data com os campos + o arquivo `file`."""
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
    """Warning quando o Vulkan do whisper-server caiu num device de software.

    No WSL2 o Mesa só expõe llvmpipe (CPU) — a GPU real é alcançável apenas via
    ROCm. Detectar isso no boot evita o sintoma "transcrição leva minutos".
    """
    for line in log_text.splitlines():
        low = line.lower()
        if "ggml_vulkan" not in low and "found device" not in low and "devices:" not in low:
            continue
        if "llvmpipe" in low or "dozen" in low or "(cpu)" in low:
            return (
                f"Vulkan SEM GPU real ({line.strip()}) — a transcrição será MUITO lenta. "
                "No WSL2 a GPU AMD só acelera via ROCm: use openai-whisper ou CT2-ROCm "
                "(rode `make configure`)."
            )
    return None


def _parse_response(raw: str) -> str:
    """Extrai o texto do JSON `{"text": ...}` do server.

    O campo ``text`` já vem com os segmentos concatenados fielmente pelo
    próprio whisper.cpp — nunca re-segmentar/re-juntar por linha aqui: o
    re-join com espaço foi a causa de palavras cortadas ("pa lavra").
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Resposta inesperada (não-JSON) do whisper-server: {raw[:200]!r}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Resposta inesperada do whisper-server: {raw[:200]!r}")
    if "error" in data:
        raise RuntimeError(f"whisper-server retornou erro: {data['error']}")
    if "text" not in data:
        raise RuntimeError(f"Resposta do whisper-server sem campo 'text': {raw[:200]!r}")
    return str(data["text"])


class WhisperCppServerBackend:
    """Backend de transcrição com modelo quente via whisper-server.exe."""

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
            raise RuntimeError(f"whisper-server morreu (código {self._proc.returncode}).")
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
            self._language,  # "auto" ou código ISO 639-1 (pt, en, ...)
        ]
        # VAD (silero): corta por silêncio em vez de tempo fixo — evita cortar
        # no meio de palavras. Opt-in: só quando o modelo de VAD foi baixado
        # pelo setup para o mesmo diretório.
        from app.features.whispercpp import find_vad_model

        vad_model = find_vad_model(self._model.parent)
        if vad_model is not None:
            cmd += ["--vad", "--vad-model", str(vad_model)]
        print(
            f"[VoiceMate] Subindo whisper-server '{self._model.stem}' "
            f"(whisper.cpp, idioma={self._language}, vad={'on' if vad_model else 'off'})..."
        )
        # stdout/stderr vão para um arquivo de log (não DEVNULL): é onde o ggml
        # imprime qual device Vulkan foi escolhido — essencial p/ detectar o
        # llvmpipe (Vulkan por software) no WSL2.
        log_file = self._log_path.open("wb")
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(self._exe.parent),
            )
        finally:
            log_file.close()  # o filho herda o fd; o nosso handle pode fechar
        self._wait_ready()
        self._report_vulkan_device()
        self._warmup()
        print("[VoiceMate] Modelo pronto (quente).")

    def _report_vulkan_device(self) -> None:
        """Loga o device Vulkan escolhido e avisa se for software (llvmpipe)."""
        try:
            log_text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for line in log_text.splitlines():
            if "ggml_vulkan" in line.lower() and "device" in line.lower():
                print(f"[VoiceMate] {line.strip()}")
                break
        warning = _vulkan_device_warning(log_text)
        if warning:
            print(f"[VoiceMate] ⚠ {warning}", file=sys.stderr)

    def _wait_ready(self, timeout: float = _READY_TIMEOUT_SECONDS) -> None:
        """Espera o server aceitar conexões (modelo já carregado nesse ponto)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(f"whisper-server saiu com código {self._proc.returncode} ao iniciar.")
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=1.0):
                    return
            except OSError:
                time.sleep(0.2)
        raise TimeoutError(f"whisper-server não respondeu em {timeout:.0f}s.")

    def _warmup(self) -> None:
        """Inferência de silêncio p/ compilar shaders Vulkan fora do 1º turno real."""
        silence = np.zeros(int(self._sample_rate * _WARMUP_SECONDS), dtype=np.float32)
        try:
            self._infer(silence)
        except Exception as exc:  # noqa: BLE001
            print(f"[VoiceMate] ⚠ warmup do whisper-server falhou (seguindo): {exc}", file=sys.stderr)

    def _infer(self, audio: NDArray[np.float32]) -> str:
        wav_bytes = _audio_to_wav_bytes(audio, self._sample_rate)
        fields = {"temperature": "0.0", "response_format": "json", "language": self._language}
        body, content_type = _build_multipart(fields, "audio.wav", wav_bytes)
        req = urllib.request.Request(  # noqa: S310 — URL fixa em loopback
            f"{self._base_url}/inference",
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_INFER_TIMEOUT_SECONDS) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
        return _parse_response(raw)
