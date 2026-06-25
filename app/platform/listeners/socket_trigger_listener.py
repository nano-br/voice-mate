"""Trigger via local HTTP daemon — the WSL2 path (and the automation path).

The app runs as a daemon inside WSL and listens on 127.0.0.1:<port>. The trigger
comes from outside: the Windows-side script (scripts/windows/voicemate-hotkeys.ahk
or .ps1) registers the global hotkeys and does `POST /trigger {"flow": ...}`.
WSL2's `localhostForwarding` exposes the port to Windows automatically.

The protocol carries STATE (fixes the silent toggle desync): `/trigger` returns
WHAT IT DID (`action` started/stopped/restarted + `op_seq`), and there is a state
hub (`SessionStatus`) that consumers poll. Each consumer registers
(`/register` → `client_id`) and picks the mode via `?scope=`:
  - `all`  (default) — the GLOBAL state/result; the monotonic `seq`/`op_seq`
    tells whether it's newer than the last one kept;
  - `mine` — only what THIS `client_id` started.

Endpoints:
  - POST /register                       → 200 {"client_id": "..."}
  - POST /trigger  {"flow"?, "client_id"?}  (or GET /trigger?flow=&client_id=)
                   → 200 {"ok", "flow", "client_id", "action", "op_seq", "state"}
  - GET  /status?client_id=&scope=       → 200 {"state", "op_seq", "flow",
                   "client_id", "is_yours", "result_seq", "scope"}
  - GET  /result?client_id=&scope=       → 200 {"seq", "text", "op_seq",
                   "client_id", "scope"}  — for Windows to set the native clipboard
  - GET  /health                         → 200 {"status": "ok", "flows": [...]}

Keeps the "stop decides the destination" design: each request is equivalent to
pressing that flow's hotkey — the callback is the same `session.toggle(flow)` as
the other listeners, now returning the operation.
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

from app.i18n import _

if TYPE_CHECKING:
    from app.core.session_status import Scope, SessionStatus, ToggleOutcome

DEFAULT_DAEMON_PORT = 47821

# Per-flow binding: receives the client_id (who fired it) and returns what the
# toggle did. The start/stop decision is synchronous — only transcription is
# async — so the request can answer the operation without waiting for the STT.
TriggerBinding = Callable[[str | None], "ToggleOutcome | None"]


def _scope_of(value: str | None) -> Scope:
    return "mine" if value == "mine" else "all"


class SocketTriggerListener:
    """Local HTTP server that maps trigger requests to per-flow callbacks."""

    def __init__(
        self,
        bindings: dict[str, TriggerBinding],
        port: int = DEFAULT_DAEMON_PORT,
        host: str = "127.0.0.1",
        status: SessionStatus | None = None,
    ) -> None:
        if not bindings:
            raise ValueError("SocketTriggerListener requires at least one binding")
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
            # Ephemeral port (0) → expose the real port chosen by the OS.
            self._port = self._server.server_address[1]
        self._server.serve_forever(poll_interval=0.5)

    def reinstall(self) -> None:
        """No-op: there's no OS hook to reinstall."""
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
        def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler signature
            parsed = urlparse(self.path)
            if parsed.path == "/register":
                self._handle_register()
                return
            if parsed.path != "/trigger":
                self._respond(404, {"error": f"unknown route: {self.path}"})
                return
            payload = self._read_json_body()
            if payload is None:
                return  # _read_json_body already answered the error
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
                    self._respond(404, {"error": "status unavailable"})
                    return
                self._respond(200, status.status(client_id, scope))
                return
            if parsed.path == "/result":
                # Latest transcription/response, for Windows to set the native
                # clipboard (Set-Clipboard) — robust where WSL fails. With `since`,
                # returns the NEXT unseen one (drains in order, without losing
                # intermediate ones when the producer runs faster than the polling).
                if status is None:
                    self._respond(404, {"error": "status unavailable"})
                    return
                since_raw = query.get("since", [None])[0]
                since = int(since_raw) if since_raw is not None and since_raw.lstrip("-").isdigit() else None
                self._respond(200, status.result(client_id, scope, since))
                return
            if parsed.path == "/trigger":
                flow = query.get("flow", [default_flow])[0]
                self._dispatch(flow, client_id)
                return
            self._respond(404, {"error": f"unknown route: {parsed.path}"})

        def _read_json_body(self) -> dict[str, object] | None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                self._respond(400, {"error": "invalid body (expected JSON)"})
                return None
            return parsed if isinstance(parsed, dict) else {}

        def _handle_register(self) -> None:
            if status is None:
                self._respond(404, {"error": "registration unavailable"})
                return
            self._respond(200, {"client_id": status.register()})

        def _dispatch(self, flow: str, client_id: str | None) -> None:
            binding = bindings.get(flow)
            if binding is None:
                self._respond(404, {"error": f"unknown flow: {flow}", "flows": list(bindings)})
                return
            # Auto-registration: a consumer may fire without registering first; in
            # that case we mint a client_id and return it so it can start using it.
            if client_id is None and status is not None:
                client_id = status.register()
            try:
                outcome = binding(client_id)
            except Exception:  # noqa: BLE001 — request boundary: log + respond with error
                print(_("[VoiceMate] ❌ Error in trigger for flow '{flow}':").format(flow=flow), file=sys.stderr)
                traceback.print_exc()
                self._respond(500, {"ok": False, "flow": flow, "error": "error processing the trigger"})
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

        def log_message(self, *args: object) -> None:  # silence the default per-request log
            return

    return _TriggerHandler
