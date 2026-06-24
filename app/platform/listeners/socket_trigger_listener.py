"""Gatilho via daemon HTTP local — o caminho do WSL2 (e de automações).

O app roda como daemon dentro do WSL e escuta em 127.0.0.1:<porta>. O gatilho
vem de fora: o script do lado Windows (scripts/windows/voicemate-hotkeys.ahk
ou .ps1) registra os hotkeys globais e faz `POST /trigger {"flow": ...}`.
O `localhostForwarding` do WSL2 expõe a porta ao Windows automaticamente.

O protocolo carrega ESTADO (conserta o desync silencioso do toggle): o `/trigger`
devolve O QUE FEZ (`action` started/stopped/restarted + `op_seq`), e há um hub de
estado (`SessionStatus`) que os consumidores consultam. Cada consumidor se
registra (`/register` → `client_id`) e escolhe a modalidade via `?scope=`:
  - `all`  (default) — o estado/resultado GLOBAL; o `seq`/`op_seq` monotônico
    diz se é mais novo que o último guardado;
  - `mine` — só o que ESTE `client_id` iniciou.

Endpoints:
  - POST /register                       → 200 {"client_id": "..."}
  - POST /trigger  {"flow"?, "client_id"?}  (ou GET /trigger?flow=&client_id=)
                   → 200 {"ok", "flow", "client_id", "action", "op_seq", "state"}
  - GET  /status?client_id=&scope=       → 200 {"state", "op_seq", "flow",
                   "client_id", "is_yours", "result_seq", "scope"}
  - GET  /result?client_id=&scope=       → 200 {"seq", "text", "op_seq",
                   "client_id", "scope"}  — p/ o Windows setar o clipboard nativo
  - GET  /health                         → 200 {"status": "ok", "flows": [...]}

Mantém o desenho "stop decide o destino": cada request equivale a apertar o
hotkey daquele flow — o callback é o mesmo `session.toggle(flow)` dos outros
listeners, agora devolvendo a operação.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from app.core.session_status import Scope, SessionStatus, ToggleOutcome

DEFAULT_DAEMON_PORT = 47821

# Binding por flow: recebe o client_id (quem disparou) e devolve o que o toggle
# fez. A decisão start/stop é síncrona — só a transcrição é assíncrona —, então
# o request já responde a operação sem esperar o STT.
TriggerBinding = Callable[[str | None], "ToggleOutcome | None"]


def _scope_of(value: str | None) -> Scope:
    return "mine" if value == "mine" else "all"


class SocketTriggerListener:
    """Servidor HTTP local que mapeia requests de gatilho para callbacks por flow."""

    def __init__(
        self,
        bindings: dict[str, TriggerBinding],
        port: int = DEFAULT_DAEMON_PORT,
        host: str = "127.0.0.1",
        status: SessionStatus | None = None,
    ) -> None:
        if not bindings:
            raise ValueError("SocketTriggerListener exige ao menos um binding")
        self._bindings = dict(bindings)
        self._default_flow = next(iter(bindings))
        self._host = host
        self._port = port
        self._status = status
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self._port

    @property
    def flows(self) -> list[str]:
        return list(self._bindings)

    def listen(self, on_toggle: Callable[[], None] | None = None) -> None:
        handler_cls = _build_handler(self._bindings, self._default_flow, self._status)
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
    bindings: dict[str, TriggerBinding],
    default_flow: str,
    status: SessionStatus | None = None,
) -> type[BaseHTTPRequestHandler]:
    class _TriggerHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — assinatura do BaseHTTPRequestHandler
            parsed = urlparse(self.path)
            if parsed.path == "/register":
                self._handle_register()
                return
            if parsed.path != "/trigger":
                self._respond(404, {"error": f"rota desconhecida: {self.path}"})
                return
            payload = self._read_json_body()
            if payload is None:
                return  # _read_json_body já respondeu o erro
            flow = str(payload.get("flow", default_flow))
            client_id = payload.get("client_id")
            self._dispatch(flow, str(client_id) if client_id is not None else None)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            client_id = query.get("client_id", [None])[0]
            scope = _scope_of(query.get("scope", [None])[0])
            if parsed.path == "/health":
                self._respond(200, {"status": "ok", "flows": list(bindings)})
                return
            if parsed.path == "/status":
                if status is None:
                    self._respond(404, {"error": "status indisponível"})
                    return
                self._respond(200, status.status(client_id, scope))
                return
            if parsed.path == "/result":
                # Última transcrição/resposta, p/ o Windows setar o clipboard
                # nativo (Set-Clipboard) — robusto onde o WSL falha. Com `since`,
                # devolve o PRÓXIMO não-visto (drena em ordem, sem perder os
                # intermediários quando o produtor vai mais rápido que o polling).
                if status is None:
                    self._respond(404, {"error": "status indisponível"})
                    return
                since_raw = query.get("since", [None])[0]
                since = int(since_raw) if since_raw is not None and since_raw.lstrip("-").isdigit() else None
                self._respond(200, status.result(client_id, scope, since))
                return
            if parsed.path == "/trigger":
                flow = query.get("flow", [default_flow])[0]
                self._dispatch(flow, client_id)
                return
            self._respond(404, {"error": f"rota desconhecida: {parsed.path}"})

        def _read_json_body(self) -> dict[str, object] | None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                self._respond(400, {"error": "body inválido (esperado JSON)"})
                return None
            return parsed if isinstance(parsed, dict) else {}

        def _handle_register(self) -> None:
            if status is None:
                self._respond(404, {"error": "registro indisponível"})
                return
            self._respond(200, {"client_id": status.register()})

        def _dispatch(self, flow: str, client_id: str | None) -> None:
            binding = bindings.get(flow)
            if binding is None:
                self._respond(404, {"error": f"flow desconhecido: {flow}", "flows": list(bindings)})
                return
            # Auto-registro: um consumidor pode disparar sem registrar antes; nesse
            # caso emitimos um client_id e o devolvemos, para ele passar a usá-lo.
            if client_id is None and status is not None:
                client_id = status.register()
            try:
                outcome = binding(client_id)
            except Exception:  # noqa: BLE001 — fronteira de request: logar + responder erro
                print(f"[VoiceMate] ❌ Erro no trigger do flow '{flow}':", file=sys.stderr)
                traceback.print_exc()
                self._respond(500, {"ok": False, "flow": flow, "error": "erro ao processar o trigger"})
                return
            body: dict[str, object] = {"ok": True, "flow": flow, "client_id": client_id}
            if outcome is not None:
                body.update({"action": outcome.action, "op_seq": outcome.op_seq, "state": outcome.state})
            self._respond(200, body)

        def _respond(self, status_code: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:  # silencia o log default por request
            return

    return _TriggerHandler
