"""Daemon API v2 client (stdlib `urllib`) and the `/events` long-poll thread.

- Loopback only, never through a system proxy (`ProxyHandler({})`).
- A refused loopback connect takes ~2 s on Windows, so `probe` (a 500 ms TCP connect)
  tells "nothing is listening" apart quickly, and a timeout is double-checked with it.
- Bearer token: fetched lazily from a provider (the token file, read through `wsl.exe` in
  WSL modes), cached, and read again after a 401.
- Every error surfaces as `DaemonError` with a kind (offline / timeout / http / protocol);
  error bodies are English and only logged, never shown to users.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Literal, cast
from urllib.parse import urlencode

from app.companion.version import companion_version
from app.protocol.models import (
    AckRequest,
    AckResponse,
    CancelRequest,
    CancelResponse,
    Capability,
    EventsResponse,
    HealthPayload,
    RegisterRequest,
    RegisterResponse,
    ResultsQuery,
    ResultsResponse,
    ShutdownReason,
    TriggerRequest,
    TriggerResponse,
)

log = logging.getLogger(__name__)

COMPANION_NAME: Final = "voicemate-companion"
# Derived from pyproject.toml (app/companion/version.py), never written here a second time.
COMPANION_VERSION: Final = companion_version()
DEFAULT_HOST: Final = "127.0.0.1"
PROBE_TIMEOUT_S: Final = 0.5
HEALTH_TIMEOUT_S: Final = 2.0
REQUEST_TIMEOUT_S: Final = 3.0
EVENTS_WAIT_S: Final = 25
EVENTS_TIMEOUT_SLACK_S: Final = 5.0
LEASE_S: Final = 40
TOKEN_REREAD_S: Final = 30.0  # after a 401, the token is read again at most this often
TOKEN_WAIT_S: Final = 25.0  # a request waits this long for a first token read in progress
UNREGISTER_ON_STOP_TIMEOUT_S: Final = 1.0

ErrorKind = Literal["offline", "timeout", "http", "protocol"]
TokenProvider = Callable[[], str | None]


class DaemonError(Exception):
    def __init__(
        self, kind: ErrorKind, status: int = 0, body: Mapping[str, object] | None = None, detail: str = ""
    ) -> None:
        self.kind: ErrorKind = kind
        self.status = status
        self.body: Mapping[str, object] = body or {}
        error = self.body.get("error", "")
        super().__init__(f"{kind} {status} {error or detail}".strip())


def _json_object(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DaemonError("protocol", detail=f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise DaemonError("protocol", detail="expected a JSON object")
    return value


def _require(payload: Mapping[str, object], *keys: str) -> None:
    missing = [key for key in keys if key not in payload]
    if missing:
        raise DaemonError("protocol", detail=f"missing fields: {', '.join(missing)}")


class DaemonClient:
    def __init__(self, port: int, *, host: str = DEFAULT_HOST, token_provider: TokenProvider | None = None) -> None:
        self.host = host
        self.port = port
        self._base = f"http://{host}:{port}"
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self._token_provider = token_provider
        self._token_cond = threading.Condition()
        self._token: str | None = None
        self._token_loaded = False
        self._token_reading = False
        self._token_read_at: float | None = None
        # Set by /health (`auth: true`) or by a 401: only then is the token file read.
        self.auth_required = False

    # --- token ---------------------------------------------------------------------------------

    def token(self) -> str | None:
        """The bearer token (None while the daemon does not require one).

        The provider may be slow (`wsl.exe`): it runs outside the lock, and other requests
        meanwhile use the token they already have (they only wait when there is none yet)."""
        with self._token_cond:
            if not self.auth_required:
                return None
            if self._token_loaded:
                return self._token
            if self._token_reading:
                if self._token is None:
                    self._token_cond.wait_for(lambda: not self._token_reading, timeout=TOKEN_WAIT_S)
                return self._token
            self._token_reading = True
        value: str | None = None
        try:
            if self._token_provider is not None:
                value = self._token_provider()
        except Exception:  # noqa: BLE001 - a missing token only means 401s
            log.exception("reading the API token failed")
        finally:
            with self._token_cond:
                self._token = value
                self._token_loaded = True
                self._token_reading = False
                self._token_read_at = time.monotonic()
                self._token_cond.notify_all()
        return value

    def invalidate_token(self) -> bool:
        """Read the token again on next use, at most every TOKEN_REREAD_S (a persistent 401
        must not run `wsl.exe` on every request). True = it will be read again."""
        with self._token_cond:
            if self._token_read_at is not None and time.monotonic() - self._token_read_at < TOKEN_REREAD_S:
                return False
            self._token_loaded = False
            return True

    # --- transport -------------------------------------------------------------------------------

    def probe(self, timeout: float = PROBE_TIMEOUT_S) -> bool:
        """Is something listening? (fast: no ~2 s wait for a refused loopback connect)."""
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        body: Mapping[str, object] | None = None,
        *,
        timeout: float = REQUEST_TIMEOUT_S,
        auth: bool = True,
        retry_unauthorized: bool = True,
    ) -> dict[str, object]:
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if method == "POST":
            data = json.dumps(dict(body or {})).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            token = self.token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self._base + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                error_body = _json_object(exc.read())
            except (DaemonError, OSError):
                error_body = {}
            finally:
                exc.close()
            if exc.code == 401 and auth and retry_unauthorized:
                self.auth_required = True
                if self.invalidate_token():  # the token file may have been recreated
                    return self._request(method, path, body, timeout=timeout, auth=auth, retry_unauthorized=False)
            raise DaemonError("http", exc.code, error_body) from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError):
                raise self._timeout_error(str(reason)) from None
            raise DaemonError("offline", detail=str(reason)) from None
        except TimeoutError as exc:
            raise self._timeout_error(str(exc)) from None
        except (http.client.HTTPException, OSError) as exc:
            raise DaemonError("offline", detail=str(exc)) from None
        return _json_object(raw)

    def _timeout_error(self, detail: str) -> DaemonError:
        # A timeout can hide a refusal (both take ~2 s on Windows loopback): look again.
        return DaemonError("timeout" if self.probe() else "offline", detail=detail)

    # --- endpoints ---------------------------------------------------------------------------------

    def health(self, timeout: float = HEALTH_TIMEOUT_S) -> HealthPayload:
        payload = self._request("GET", "/health", timeout=timeout, auth=False)
        _require(payload, "status")
        required = payload.get("auth") is True
        if required and not self.auth_required:
            self.invalidate_token()
        self.auth_required = required or self.auth_required
        return cast(HealthPayload, payload)

    def register(self, body: RegisterRequest) -> RegisterResponse:
        payload = self._request("POST", "/register", cast(Mapping[str, object], body))
        _require(payload, "client_id", "instance", "granted", "cursor", "snapshot")
        return cast(RegisterResponse, payload)

    def unregister(self, client_id: str, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self._request("POST", "/unregister", {"client_id": client_id}, timeout=timeout)

    def events(self, client_id: str, instance: str, since: int, wait: int = EVENTS_WAIT_S) -> EventsResponse:
        query = urlencode({"client_id": client_id, "instance": instance, "since": since, "wait": wait})
        payload = self._request("GET", f"/events?{query}", timeout=wait + EVENTS_TIMEOUT_SLACK_S)
        _require(payload, "instance", "cursor", "events")
        return cast(EventsResponse, payload)

    def trigger(self, body: TriggerRequest) -> TriggerResponse:
        if not self.probe():
            raise DaemonError("offline", detail="nothing is listening")
        payload = self._request("POST", "/trigger", cast(Mapping[str, object], body))
        _require(payload, "action")
        return cast(TriggerResponse, payload)

    def cancel(self, body: CancelRequest) -> CancelResponse:
        payload = self._request("POST", "/cancel", cast(Mapping[str, object], body))
        return cast(CancelResponse, payload)

    def shutdown(self, reason: ShutdownReason, timeout: float = 2.0) -> None:
        self._request("POST", "/shutdown", {"reason": reason}, timeout=timeout)

    def ack(self, body: AckRequest) -> AckResponse:
        payload = self._request("POST", "/results/ack", cast(Mapping[str, object], body))
        return cast(AckResponse, payload)

    def results(self, state: ResultsQuery, limit: int = 10) -> ResultsResponse:
        payload = self._request("GET", f"/results?{urlencode({'state': state, 'limit': limit})}")
        _require(payload, "instance", "results")
        return cast(ResultsResponse, payload)


# --- long poll ---------------------------------------------------------------------------------------


@dataclass
class Session:
    client_id: str
    instance: str
    cursor: int
    granted: frozenset[Capability]
    lease_s: int


class EventPoller:
    """Registers and long-polls `/events` on its own thread while enabled.

    Registers again when the daemon forgets the client (410), when the instance changes
    (a `reset` for a new daemon), and when a lease it was granted is missing from a
    response (it expired while the client stalled: take it over with the same key).
    Callbacks run on the poller thread: post them to the dispatcher.
    """

    def __init__(
        self,
        client: DaemonClient,
        *,
        registration: Callable[[], RegisterRequest],
        on_registered: Callable[[RegisterResponse], None],
        on_events: Callable[[EventsResponse, bool], None],
        on_unauthorized: Callable[[], None] | None = None,
        wait_s: int = EVENTS_WAIT_S,
        retry_s: float = 1.0,
    ) -> None:
        self._client = client
        self._registration = registration
        self._on_registered = on_registered
        self._on_events = on_events
        self._on_unauthorized = on_unauthorized
        self._wait_s = wait_s
        self._retry_s = retry_s
        self._lock = threading.Lock()
        self._session: Session | None = None
        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="companion-events", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._enabled.set()  # wake the idle wait

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def session(self) -> Session | None:
        with self._lock:
            return self._session

    def invalidate(self, client_id: str | None = None) -> None:
        """Register again before the next poll (e.g. an ACK got 410)."""
        with self._lock:
            if self._session is not None and (client_id is None or self._session.client_id == client_id):
                self._session = None

    def take_session(self) -> Session | None:
        """Detach the session (Quit): the caller unregisters it."""
        with self._lock:
            session, self._session = self._session, None
            return session

    def _pause(self) -> None:
        self._stop.wait(self._retry_s)

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._enabled.wait(1.0) or self._stop.is_set():
                continue
            try:
                self._step()
            except DaemonError as exc:
                if exc.kind == "http" and exc.status == 410:
                    self.invalidate()
                    continue
                if exc.kind == "http" and exc.status == 401 and self._on_unauthorized is not None:
                    self._on_unauthorized()
                log.debug("events: %s", exc)
                self._pause()
            except Exception:  # noqa: BLE001 - this thread must survive anything
                log.exception("events poller failed; retrying")
                self._pause()

    def _step(self) -> None:
        session = self.session()
        if session is None:
            registered = self._client.register(self._registration())
            granted: list[Capability] = list(registered.get("granted", []))
            session = Session(
                client_id=str(registered["client_id"]),
                instance=str(registered["instance"]),
                cursor=int(registered["cursor"]),
                granted=frozenset(granted),
                lease_s=int(registered.get("lease_s", LEASE_S)),
            )
            with self._lock:  # take_session() (Quit) sees it, or we see the stop: never neither
                stopped = self._stop.is_set()
                if not stopped:
                    self._session = session
            if stopped:
                # Quit while /register was in flight: release the leases at once, or the
                # daemon would keep them for this client (and skip its own beeps) until they expire.
                try:
                    self._client.unregister(session.client_id, timeout=UNREGISTER_ON_STOP_TIMEOUT_S)
                except DaemonError as exc:
                    log.info("unregister after stop failed: %s", exc)
                return
            self._on_registered(registered)
            return
        response = self._client.events(session.client_id, session.instance, session.cursor, self._wait_s)
        with self._lock:
            if self._session is not session or self._stop.is_set():
                return  # invalidated or quitting meanwhile
            instance_changed = response["instance"] != session.instance
            session.cursor = int(response["cursor"])
            session.instance = str(response["instance"])
        self._on_events(response, instance_changed)
        lost = session.granted - frozenset(response.get("leases", []))
        if instance_changed or lost:
            if lost:
                log.info("events: lease(s) %s expired; registering again", sorted(lost))
            self.invalidate(session.client_id)
