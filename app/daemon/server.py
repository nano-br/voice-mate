"""HTTP API of the daemon: the v1 routes of the Windows scripts plus the API v2.

Bound on 127.0.0.1 BEFORE the model loads (see `app.daemon.lifecycle`): until
the engine is attached and the lifecycle is ready, `/health` answers
`ready: false` and every other route except `/shutdown` and `/unregister`
answers 503 `{"error": "not ready"}`. On WSL2 this server IS the trigger (the
`SocketTriggerListener` wraps it); with `--api` it runs next to the native
hotkey listener.

Routes (payloads in `app.protocol.models`, semantics in docs/companion-app.md):

  v1 (the Windows scripts; they keep working against a daemon without a token)
    POST /register  (empty body)            -> {"client_id", ...}
    POST /trigger   {"flow"?, "client_id"?} -> {"ok", "flow", "client_id", "action", "op_seq", "state"}
    GET  /status?client_id=&scope=all|mine  -> live state of the current/your operation
    GET  /result?client_id=&scope=&since=   -> next unseen result (or error event)
  v2
    GET  /health                            -> HealthPayload (never requires the token)
    POST /register  RegisterRequest         -> RegisterResponse (leases, snapshot, cursor)
    POST /unregister UnregisterRequest      -> {"ok": true} | 410
    GET  /events?client_id=&instance=&since=&wait=  -> EventsResponse (long poll) | 410
    POST /trigger   TriggerRequest (expect)  -> TriggerResponse
    POST /cancel    CancelRequest            -> CancelResponse
    POST /results/ack AckRequest             -> AckResponse | 409 | 410
    GET  /results?state=unacked|recent&limit=&client_id=&instance=  -> ResultsResponse
    POST /shutdown  ShutdownRequest (optional body) -> {"ok": true}, then the daemon stops

Browser hardening: WSL2 forwards this port to Windows' loopback, where any web
page can reach it. Requests carrying an `Origin` header (browsers always send
it on cross-site POSTs; scripts and WinHTTP don't) or a non-loopback `Host`
(DNS rebinding) get 403, and the trigger is POST-only (a GET could be fired by
a plain `<img>` tag). With `--api-token` every route but `/health` also needs
`Authorization: Bearer <token>` (401).

Error bodies are `ErrorResponse`: English, for logs only.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
from typing import TYPE_CHECKING, Final, Protocol, cast, get_args
from urllib.parse import parse_qs, urlparse

from app.daemon.auth import bearer_matches
from app.daemon.hub import InstanceMismatch, UnknownClient
from app.daemon.lifecycle import Lifecycle
from app.i18n import _, active_language
from app.protocol.models import (
    API_VERSION,
    AckStatus,
    Capability,
    FlowInfo,
    HealthPayload,
    PlatformName,
    ResultsQuery,
    ShutdownReason,
    TriggerExpect,
    TriggerName,
    TriggerResponse,
)

if TYPE_CHECKING:
    from app.core.session_status import CancelOutcome, Scope, SessionStatus, ToggleOutcome

DEFAULT_DAEMON_PORT: Final = 47821
MAX_WAIT_S: Final = 25.0  # longest /events long poll; clients use wait + 5 s as their HTTP timeout
_MAX_BODY: Final = 256 * 1024

_LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})
_GET_ROUTES: Final = frozenset({"/health", "/status", "/result", "/events", "/results"})
_POST_ROUTES: Final = frozenset({"/register", "/unregister", "/trigger", "/cancel", "/results/ack", "/shutdown"})
_OPEN_ROUTES: Final = frozenset({"/health"})  # no token needed
_BEFORE_READY_ROUTES: Final = frozenset({"/health", "/shutdown", "/unregister"})  # Quit works while loading

_CAPABILITIES: Final[tuple[Capability, ...]] = get_args(Capability)
_TRIGGER_EXPECTS: Final[tuple[TriggerExpect, ...]] = get_args(TriggerExpect)
_ACK_STATUSES: Final[tuple[AckStatus, ...]] = get_args(AckStatus)
_RESULTS_QUERIES: Final[tuple[ResultsQuery, ...]] = get_args(ResultsQuery)
_SHUTDOWN_REASONS: Final[tuple[ShutdownReason, ...]] = get_args(ShutdownReason)


class EngineControl(Protocol):
    """What the API drives: the recording session."""

    def toggle(
        self, handler_id: str, client_id: str | None = None, expect: TriggerExpect = "toggle"
    ) -> ToggleOutcome | None: ...

    def cancel(self, op_seq: int | None = None) -> CancelOutcome: ...


@dataclass(frozen=True)
class EngineInfo:
    """What `/health` reports about the engine (provisional until the engine is built)."""

    platform: PlatformName
    trigger: TriggerName
    flows: tuple[FlowInfo, ...] = ()
    tts: bool = False

    @property
    def flow_names(self) -> list[str]:
        return [flow["name"] for flow in self.flows]


@lru_cache(maxsize=1)
def engine_version() -> str:
    try:
        return metadata.version("voice-mate")
    except metadata.PackageNotFoundError:
        return "0.0.0"


@dataclass(frozen=True)
class _Reply:
    status: int
    body: Mapping[str, object]
    headers: tuple[tuple[str, str], ...] = ()
    after: Callable[[], None] | None = None  # runs once the response was sent


def _error(status: int, message: str, **extra: object) -> _Reply:
    return _Reply(status, {"error": message, **extra})


_GONE: Final = _error(410, "unknown client_id (register again)")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int_param(raw: str | None) -> int | None:
    if raw is None or not raw.lstrip("-").isdigit():
        return None
    return int(raw)


def _float_param(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _loopback_host(host: str | None) -> bool:
    if host is None:
        return True
    try:
        hostname = urlparse(f"//{host}").hostname
    except ValueError:  # malformed (e.g. unbalanced IPv6 brackets)
        return False
    return hostname in _LOOPBACK_HOSTS


class ApiServer:
    """The daemon's HTTP server. `start()` serves in a background thread."""

    def __init__(
        self,
        status: SessionStatus,
        *,
        info: EngineInfo,
        control: EngineControl | None = None,
        port: int = DEFAULT_DAEMON_PORT,
        host: str = "127.0.0.1",
        token: str | None = None,
        lifecycle: Lifecycle | None = None,
        on_shutdown: Callable[[ShutdownReason], None] | None = None,
    ) -> None:
        self._status = status
        self._hub = status.hub
        self._info = info
        self._control = control
        self._host = host
        self._port = port
        self._token = token
        self._lifecycle = lifecycle
        self._on_shutdown = on_shutdown
        self._lock = threading.Lock()
        self._server: _HttpServer | None = None
        self._serving: threading.Thread | None = None
        self._stopped = threading.Event()
        self._closed = False  # stopped for good: start()/bind() never reopen it

    # -- lifecycle -------------------------------------------------------------
    @property
    def port(self) -> int:
        return self._port

    @property
    def ready(self) -> bool:
        if self._control is None:
            return False
        return self._lifecycle is None or self._lifecycle.ready

    def attach(self, control: EngineControl, info: EngineInfo) -> None:
        """The engine is built: serve its flows (still 503 until the lifecycle is ready)."""
        with self._lock:
            self._info = info
            self._control = control

    def bind(self) -> None:
        """Open the listening socket (raises OSError, e.g. when the port is taken)."""
        with self._lock:
            if self._server is not None or self._closed:
                return
            self._server = _HttpServer((self._host, self._port), self)
            # Ephemeral port (0): expose the real port chosen by the OS.
            self._port = self._server.server_address[1]

    def start(self) -> None:
        self.bind()
        with self._lock:
            server = self._server
            if server is None or self._serving is not None:
                return
            self._serving = threading.Thread(target=self._serve, args=(server,), daemon=True, name="DaemonApi")
            self._serving.start()

    def _serve(self, server: _HttpServer) -> None:
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            self._stopped.set()

    def wait_stopped(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)

    def stop(self, grace: float = 1.0) -> None:
        """Stop serving; requests in flight (woken long polls) get `grace` seconds to finish."""
        with self._lock:
            server = self._server
            serving = self._serving
            self._server = None
            self._serving = None
            self._closed = True
        if server is not None:
            if serving is not None:
                server.shutdown()  # never called from the serving thread: requests run on their own
            server.wait_idle(grace)
            server.server_close()
        self._stopped.set()

    # -- dispatch ----------------------------------------------------------------
    def dispatch(self, method: str, raw_path: str, headers: Mapping[str, str], body: bytes) -> _Reply:
        """Answer one request. `headers` keys are lower-case."""
        if headers.get("origin") is not None:
            return _error(403, "cross-origin requests are not allowed")
        if not _loopback_host(headers.get("host")):
            return _error(403, "unexpected Host header")
        parsed = urlparse(raw_path)
        path = parsed.path
        routes, other, other_method = (
            (_GET_ROUTES, _POST_ROUTES, "POST") if method == "GET" else (_POST_ROUTES, _GET_ROUTES, "GET")
        )
        if path not in routes:
            if path in other:
                return _error(405, f"use {other_method} {path}")
            return _error(404, f"unknown route: {path}")
        if path not in _OPEN_ROUTES and self._token is not None:
            if not bearer_matches(headers.get("authorization"), self._token):
                return _Reply(401, {"error": "unauthorized"}, headers=(("WWW-Authenticate", "Bearer"),))
        if path not in _BEFORE_READY_ROUTES and not self.ready:
            return _Reply(503, {"error": "not ready"}, headers=(("Retry-After", "2"),))
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        payload: dict[str, object] = {}
        if method == "POST":
            try:
                payload = self._parse_body(body)
            except ValueError:
                return _error(400, "invalid body (expected JSON)")
            client_id = payload.get("client_id")
        else:
            client_id = query.get("client_id")
        if isinstance(client_id, str) and client_id and path not in ("/events", "/unregister"):
            self._hub.touch(client_id)  # any request carrying a client_id renews its leases
        return self._route(path, query, payload)

    @staticmethod
    def _parse_body(body: bytes) -> dict[str, object]:
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        parsed = json.loads(text)  # JSONDecodeError is a ValueError
        return cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}

    def _route(self, path: str, query: dict[str, str], payload: dict[str, object]) -> _Reply:
        if path == "/health":
            return _Reply(200, self.health())
        if path == "/status":
            return _Reply(200, self._status.status(query.get("client_id"), self._scope(query)))
        if path == "/result":
            since = _int_param(query.get("since"))
            return _Reply(200, self._status.result(query.get("client_id"), self._scope(query), since))
        if path == "/events":
            return self._events(query)
        if path == "/results":
            return self._results(query)
        if path == "/register":
            return self._register(payload)
        if path == "/unregister":
            return self._unregister(payload)
        if path == "/trigger":
            return self._trigger(payload)
        if path == "/cancel":
            return self._cancel(payload)
        if path == "/results/ack":
            return self._ack(payload)
        return self._shutdown(payload)

    @staticmethod
    def _scope(query: dict[str, str]) -> Scope:
        return "mine" if query.get("scope") == "mine" else "all"

    # -- routes ------------------------------------------------------------------
    def health(self) -> HealthPayload:
        with self._lock:
            info = self._info
        return {
            "status": "ok",
            "api_version": API_VERSION,
            "version": engine_version(),
            "instance": self._hub.instance,
            "pid": os.getpid(),
            "uptime_s": self._hub.uptime_s,
            "ready": self.ready,
            "audio": self._hub.audio,
            "lang": active_language(),
            "platform": info.platform,
            "trigger": info.trigger,
            "flows": info.flow_names,
            "flow_info": list(info.flows),
            "tts": info.tts,
            "auth": self._token is not None,
        }

    def _events(self, query: dict[str, str]) -> _Reply:
        client_id = query.get("client_id")
        if not client_id:
            return _error(400, "client_id is required")
        wait = max(0.0, min(MAX_WAIT_S, _float_param(query.get("wait"), 0.0)))
        try:
            response = self._hub.poll(
                client_id, instance=query.get("instance"), since=_int_param(query.get("since")), wait=wait
            )
        except UnknownClient:
            return _GONE
        return _Reply(200, response)

    def _results(self, query: dict[str, str]) -> _Reply:
        which = query.get("state", "unacked")
        if which not in _RESULTS_QUERIES:
            return _error(400, "state must be 'unacked' or 'recent'")
        limit = 10
        if "limit" in query:
            parsed = _int_param(query["limit"])
            if parsed is None:
                return _error(400, "limit must be an integer")
            limit = parsed
        client_id = query.get("client_id")
        if client_id and not self._hub.is_known(client_id):
            return _GONE
        instance = query.get("instance")
        if instance is not None and instance != self._hub.instance:
            return _error(409, "instance mismatch", instance=self._hub.instance)
        return _Reply(200, self._hub.results(cast(ResultsQuery, which), limit))

    def _register(self, payload: dict[str, object]) -> _Reply:
        strings: dict[str, str] = {}
        for key in ("name", "version", "os", "client_key"):
            value = payload.get(key)
            if value is None:
                strings[key] = ""
            elif isinstance(value, str):
                strings[key] = value
            else:
                return _error(400, f"{key} must be a string")
        raw_caps = payload.get("capabilities", [])
        if not isinstance(raw_caps, list) or not all(isinstance(cap, str) for cap in raw_caps):
            return _error(400, "capabilities must be a list of strings")
        capabilities = [cast(Capability, cap) for cap in raw_caps if cap in _CAPABILITIES]  # unknown: ignored
        lease_s = payload.get("lease_s")
        if lease_s is not None and not _is_int(lease_s):
            return _error(400, "lease_s must be an integer")
        response = self._hub.register(
            client_key=strings["client_key"],
            name=strings["name"],
            capabilities=capabilities,
            lease_s=cast(int | None, lease_s),
        )
        return _Reply(200, response)

    def _unregister(self, payload: dict[str, object]) -> _Reply:
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            return _error(400, "client_id is required")
        name = self._hub.client_name(client_id)
        try:
            self._hub.unregister(client_id)
        except UnknownClient:
            return _GONE
        if name:
            print(_("[VoiceMate] Client {client} unregistered.").format(client=name))
        return _Reply(200, {"ok": True})

    def _trigger(self, payload: dict[str, object]) -> _Reply:
        control = self._control
        if control is None:  # pragma: no cover (guarded by the readiness gate)
            return _Reply(503, {"error": "not ready"})
        with self._lock:
            names = self._info.flow_names
        raw_flow = payload.get("flow")
        flow = str(raw_flow) if raw_flow is not None else (names[0] if names else "clipboard")
        raw_client = payload.get("client_id")
        client_id = str(raw_client) if raw_client is not None else None
        expect = payload.get("expect", "toggle")
        if expect not in _TRIGGER_EXPECTS:
            return _error(400, "expect must be 'toggle', 'start' or 'stop'")
        if flow not in names:
            return _error(404, f"unknown flow: {flow}", flows=names)
        # Auto-registration: a consumer may fire without registering first; it gets
        # a client_id back and can start using it.
        if client_id is None:
            client_id = self._status.register()
        try:
            outcome = control.toggle(flow, client_id=client_id, expect=cast(TriggerExpect, expect))
        except Exception:  # noqa: BLE001 (request boundary: log + respond with error)
            print(_("[VoiceMate] ❌ Error in trigger for flow '{flow}':").format(flow=flow), file=sys.stderr)
            traceback.print_exc()
            return _Reply(500, {"ok": False, "flow": flow, "error": "error processing the trigger"})
        if outcome is None:
            return _error(404, f"unknown flow: {flow}", flows=names)
        body: TriggerResponse = {
            "ok": True,
            "flow": flow,
            "client_id": client_id,
            "action": outcome.action,
            "op_seq": outcome.op_seq,
            "state": outcome.state,
        }
        return _Reply(200, body)

    def _cancel(self, payload: dict[str, object]) -> _Reply:
        control = self._control
        if control is None:  # pragma: no cover (guarded by the readiness gate)
            return _Reply(503, {"error": "not ready"})
        op_seq = payload.get("op_seq")
        if op_seq is not None and not _is_int(op_seq):
            return _error(400, "op_seq must be an integer")
        outcome = control.cancel(cast(int | None, op_seq))
        return _Reply(200, {"action": outcome.action, "state": outcome.state})

    def _ack(self, payload: dict[str, object]) -> _Reply:
        client_id = payload.get("client_id")
        instance = payload.get("instance")
        raw_acks = payload.get("acks")
        if not isinstance(client_id, str) or not client_id:
            return _error(400, "client_id is required")
        if not isinstance(instance, str):
            return _error(400, "instance is required")
        if not isinstance(raw_acks, list):
            return _error(400, "acks must be a list")
        acks: list[tuple[int, AckStatus]] = []
        for item in raw_acks:
            if not isinstance(item, dict):
                return _error(400, "each ack must be an object")
            result_seq = item.get("result_seq")
            status = item.get("status")
            if not _is_int(result_seq) or status not in _ACK_STATUSES:
                return _error(400, "each ack needs an integer result_seq and a valid status")
            acks.append((cast(int, result_seq), cast(AckStatus, status)))
        try:
            response = self._hub.ack(client_id, instance=instance, acks=acks)
        except UnknownClient:
            return _GONE
        except InstanceMismatch:
            return _error(409, "instance mismatch", instance=self._hub.instance)
        return _Reply(200, response)

    def _shutdown(self, payload: dict[str, object]) -> _Reply:
        raw = payload.get("reason")
        reason: ShutdownReason = cast(ShutdownReason, raw) if raw in _SHUTDOWN_REASONS else "user_quit"
        print(_("[VoiceMate] Shutdown requested over HTTP."))
        return _Reply(200, {"ok": True}, after=lambda: self._request_shutdown(reason))

    def _request_shutdown(self, reason: ShutdownReason) -> None:
        if self._on_shutdown is not None:
            self._on_shutdown(reason)
            return
        # Standalone server (no lifecycle): announce, then stop off the request thread
        # (stop() waits for the requests in flight, this one included).
        self._hub.publish_shutdown(reason)
        threading.Thread(target=self.stop, daemon=True, name="DaemonShutdown").start()


class _HttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], api: ApiServer) -> None:
        super().__init__(address, _Handler)
        self.api = api
        self._active = 0
        self._idle = threading.Condition()

    def request_started(self) -> None:
        with self._idle:
            self._active += 1

    def request_finished(self) -> None:
        with self._idle:
            self._active -= 1
            self._idle.notify_all()

    def wait_idle(self, timeout: float) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: self._active == 0, timeout)


class _Handler(BaseHTTPRequestHandler):
    def handle(self) -> None:
        server = cast(_HttpServer, self.server)
        server.request_started()
        try:
            super().handle()
        finally:
            server.request_finished()

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def _serve(self, method: str) -> None:
        api = cast(_HttpServer, self.server).api
        body = b""
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                length = 0
            if length > _MAX_BODY:
                self._send(_error(413, "body too large"))
                return
            # Always read the body, even to answer an error: an unread body can turn
            # the close into an RST on the client.
            body = self.rfile.read(length) if length > 0 else b""
        headers = {key.lower(): value for key, value in self.headers.items()}
        try:
            reply = api.dispatch(method, self.path, headers, body)
        except Exception:  # noqa: BLE001 (request boundary)
            traceback.print_exc()
            reply = _error(500, "internal error")
        self._send(reply)
        if reply.after is not None:
            reply.after()

    def _send(self, reply: _Reply) -> None:
        data = json.dumps(reply.body, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(reply.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for name, value in reply.headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client gave up (e.g. a long poll past its own timeout)

    def log_message(self, *args: object) -> None:  # silence the default per-request log
        return
