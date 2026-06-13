"""WhisperCppServerBackend: helpers puros + caminho HTTP contra um server local.

Não sobe o whisper-server real — constrói a instância via object.__new__ e
aponta o _base_url para um http.server de teste, exercitando multipart + parsing.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest

from app.features.whispercpp.server_backend import (
    WhisperCppServerBackend,
    _audio_to_wav_bytes,
    _build_multipart,
    _parse_response,
    _vulkan_device_warning,
)


def test_vulkan_warning_on_llvmpipe() -> None:
    log = "ggml_vulkan: Found 1 Vulkan devices:\nggml_vulkan: 0 = llvmpipe (LLVM 20.1.2, 256 bits) (CPU)\n"
    warning = _vulkan_device_warning(log)
    assert warning is not None
    assert "MUITO lenta" in warning
    assert "make configure" in warning


def test_vulkan_warning_none_on_real_gpu() -> None:
    log = "ggml_vulkan: 0 = AMD Radeon RX 9070 XT (RADV GFX1201) (GPU)\n"
    assert _vulkan_device_warning(log) is None


def test_vulkan_warning_none_on_empty_log() -> None:
    assert _vulkan_device_warning("loading model...\nwhisper init\n") is None


def test_parse_response_json_text_field() -> None:
    assert _parse_response('{"text": "olá mundo"}') == "olá mundo"


def test_parse_response_preserves_inner_whitespace_verbatim() -> None:
    # Regressão: o re-join por linha transformava "pa\nlavra" em "pa lavra".
    # O campo text do JSON deve ser repassado fielmente, sem re-segmentação.
    assert _parse_response('{"text": "uma palavra inteira"}') == "uma palavra inteira"
    assert _parse_response('{"text": " com  espaços   internos "}') == " com  espaços   internos "


def test_parse_response_non_json_raises() -> None:
    with pytest.raises(RuntimeError, match="não-JSON"):
        _parse_response("texto puro do server antigo")


def test_parse_response_json_without_text_raises() -> None:
    with pytest.raises(RuntimeError, match="sem campo 'text'"):
        _parse_response('{"other": 1}')


def test_parse_response_error_field_raises() -> None:
    with pytest.raises(RuntimeError, match="failed to decode audio"):
        _parse_response('{"error": "failed to decode audio"}')


def test_build_multipart_includes_fields_and_file() -> None:
    body, content_type = _build_multipart({"language": "pt"}, "audio.wav", b"RIFF")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="language"' in body
    assert b"pt" in body
    assert b'name="file"; filename="audio.wav"' in body
    assert b"RIFF" in body


def test_audio_to_wav_bytes_is_valid_riff() -> None:
    audio = np.zeros(160, dtype=np.float32)
    data = _audio_to_wav_bytes(audio, 16000)
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"


class _EchoHandler(BaseHTTPRequestHandler):
    response_body = '{"text": "olá do server"}'
    last_request_body: bytes = b""

    def do_POST(self) -> None:  # noqa: N802 — assinatura do BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_request_body = self.rfile.read(length)
        payload = self.response_body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:  # silencia logs do server de teste
        return


@pytest.fixture
def local_server() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _make_backend(base_url: str) -> WhisperCppServerBackend:
    backend = object.__new__(WhisperCppServerBackend)
    backend._base_url = base_url  # type: ignore[attr-defined]
    backend._sample_rate = 16000  # type: ignore[attr-defined]
    backend._language = "pt"  # type: ignore[attr-defined]
    backend._proc = None  # type: ignore[attr-defined]
    return backend


def test_transcribe_posts_and_parses(local_server: str) -> None:
    backend = _make_backend(local_server)
    text = backend.transcribe(np.zeros(160, dtype=np.float32))
    assert text == "olá do server"


def test_transcribe_requests_json_format(local_server: str) -> None:
    backend = _make_backend(local_server)
    backend.transcribe(np.zeros(160, dtype=np.float32))
    assert b'name="response_format"' in _EchoHandler.last_request_body
    assert b"json" in _EchoHandler.last_request_body


def test_transcribe_does_not_split_words(local_server: str) -> None:
    # Regressão: server emitindo o texto com quebras internas não pode gerar
    # espaço dentro de palavra — o texto do JSON é usado verbatim (só strip nas pontas).
    _EchoHandler.response_body = '{"text": " transcrição com palavras inteiras "}'
    try:
        backend = _make_backend(local_server)
        assert backend.transcribe(np.zeros(160, dtype=np.float32)) == "transcrição com palavras inteiras"
    finally:
        _EchoHandler.response_body = '{"text": "olá do server"}'


def test_transcribe_surfaces_server_error(local_server: str) -> None:
    _EchoHandler.response_body = '{"error": "failed to read audio"}'
    try:
        backend = _make_backend(local_server)
        with pytest.raises(RuntimeError, match="failed to read audio"):
            backend.transcribe(np.zeros(160, dtype=np.float32))
    finally:
        _EchoHandler.response_body = '{"text": "olá do server"}'
