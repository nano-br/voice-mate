"""Gatilho via daemon HTTP local — o caminho do WSL2 (e de automações).

O app roda como daemon dentro do WSL e escuta em 127.0.0.1:<porta>. O gatilho
vem de fora: o script do lado Windows (scripts/windows/voicemate-hotkeys.ahk
ou .ps1) registra os hotkeys globais e faz `POST /trigger {"flow": ...}`.
O `localhostForwarding` do WSL2 expõe a porta ao Windows automaticamente.

Endpoints:
  - POST /trigger  body JSON {"flow": "clipboard" | "claude_chat"}
                   (ou GET /trigger?flow=...; sem flow → o primeiro binding)
  - GET  /health   → 200 {"status": "ok", "flows": [...]}

Mantém o desenho "stop decide o destino": cada request equivale a apertar o
hotkey daquele flow — o callback é o mesmo `session.toggle(flow)` dos outros
listeners.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_DAEMON_PORT = 47821


class SocketTriggerListener:
    """Servidor HTTP local que mapeia requests de gatilho para callbacks por flow."""

    def __init__(
        self,
        bindings: dict[str, Callable[[], None]],
        port: int = DEFAULT_DAEMON_PORT,
        host: str = "127.0.0.1",
    ) -> None:
        if not bindings:
            raise ValueError("SocketTriggerListener exige ao menos um binding")
        self._bindings = dict(bindings)
        self._default_flow = next(iter(bindings))
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self._port

    @property
    def flows(self) -> list[str]:
        return list(self._bindings)

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        handler_cls = _build_handler(self._bindings, self._default_flow)
        with self._lock:
            self._server = ThreadingHTTPServer((self._host, self._port), handler_cls)
            # Porta efêmera (0) → expõe a porta real escolhida pelo SO.
            self._port = self._server.server_address[1]
        self._server.serve_forever(poll_interval=0.5)

    def reinstall(self) -> None:
        """No-op: não há hook de SO para reinstalar."""
        return None

    def stop(self) -> None:
        with self._lock:
            server = self._server
            self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()


def _build_handler(
    bindings: dict[str, Callable[[], None]],
    default_flow: str,
) -> type[BaseHTTPRequestHandler]:
    class _TriggerHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — assinatura do BaseHTTPRequestHandler
            if urlparse(self.path).path != "/trigger":
                self._respond(404, {"error": f"rota desconhecida: {self.path}"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            flow = default_flow
            if raw:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    self._respond(400, {"error": "body inválido (esperado JSON)"})
                    return
                flow = str(payload.get("flow", default_flow))
            self._dispatch(flow)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._respond(200, {"status": "ok", "flows": list(bindings)})
                return
            if parsed.path == "/trigger":
                query_flow = parse_qs(parsed.query).get("flow", [default_flow])[0]
                self._dispatch(query_flow)
                return
            self._respond(404, {"error": f"rota desconhecida: {parsed.path}"})

        def _dispatch(self, flow: str) -> None:
            callback = bindings.get(flow)
            if callback is None:
                self._respond(404, {"error": f"flow desconhecido: {flow}", "flows": list(bindings)})
                return
            # Responde já e roda o callback fora do ciclo do request: o gatilho
            # do lado Windows não deve esperar a transcrição terminar.
            threading.Thread(target=callback, daemon=True, name=f"Trigger-{flow}").start()
            self._respond(200, {"ok": True, "flow": flow})

        def _respond(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # silencia o log default por request
            return

    return _TriggerHandler
