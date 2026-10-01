"""ApiServer over real HTTP: readiness gate, auth, v2 routes, validation, shutdown."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from app.core.session_status import CancelOutcome, SessionStatus, ToggleOutcome
from app.daemon.lifecycle import Lifecycle
from app.daemon.server import ApiServer, EngineInfo
from app.protocol.models import FlowInfo, ShutdownReason, TriggerAction, TriggerExpect

INFO = EngineInfo(
    platform="wsl2",
    trigger="socket",
    flows=(
        FlowInfo(name="clipboard", kind="clipboard", hotkey="ctrl+alt+v"),
        FlowInfo(name="claude_chat", kind="claude_chat", hotkey="ctrl+alt+a"),
    ),
    tts=True,
)
TOKEN = "s3cret-token"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class FakeControl:
    def __init__(self) -> None:
        self.toggles: list[tuple[str, str | None, TriggerExpect]] = []
        self.cancels: list[int | None] = []
        self.fail = False

    def toggle(self, handler_id: str, client_id: str | None = None, expect: TriggerExpect = "toggle") -> ToggleOutcome:
        if self.fail:
            raise RuntimeError("toggle exploded")
        self.toggles.append((handler_id, client_id, expect))
        action: TriggerAction = "noop" if expect == "stop" else "started"
        return ToggleOutcome(action=action, op_seq=len(self.toggles), state="recording", flow=handler_id)

    def cancel(self, op_seq: int | None = None) -> CancelOutcome:
        self.cancels.append(op_seq)
        return CancelOutcome(action="cancelled", state="idle")


def call(
    server: ApiServer,
    method: str,
    path: str,
    payload: object = None,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    if method == "POST" and data is None:
        data = b""
    all_headers = {"Content-Type": "application/json", **(headers or {})}
    if token is not None:
        all_headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}{path}", data=data, headers=all_headers, method=method
    )
    try:
        with _NO_PROXY.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


MakeServer = Callable[..., ApiServer]


@pytest.fixture
def make_server() -> Iterator[MakeServer]:
    servers: list[ApiServer] = []

    def _make(
        *,
        control: FakeControl | None = None,
        attach: bool = True,
        status: SessionStatus | None = None,
        token: str | None = None,
        lifecycle: Lifecycle | None = None,
        on_shutdown: Callable[[ShutdownReason], None] | None = None,
    ) -> ApiServer:
        server = ApiServer(
            status or SessionStatus(),
            info=INFO,
            control=(control or FakeControl()) if attach else None,
            port=0,
            token=token,
            lifecycle=lifecycle,
            on_shutdown=on_shutdown,
        )
        server.start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop(grace=0.2)


# -- readiness ------------------------------------------------------------------


def test_before_the_engine_is_built_only_health_shutdown_and_unregister_answer(make_server: MakeServer) -> None:
    reasons: list[ShutdownReason] = []
    server = make_server(attach=False, on_shutdown=reasons.append)

    status, health = call(server, "GET", "/health")
    assert status == 200
    assert health["ready"] is False
    for method, path in [("GET", "/status"), ("GET", "/result"), ("GET", "/events?client_id=x"), ("GET", "/results")]:
        assert call(server, method, path) == (503, {"error": "not ready"}), path
    for path in ["/register", "/trigger", "/cancel", "/results/ack"]:
        assert call(server, "POST", path, {})[0] == 503, path
    assert call(server, "POST", "/unregister", {"client_id": "from-an-old-instance"})[0] == 410
    assert call(server, "POST", "/shutdown", {"reason": "user_quit"}) == (200, {"ok": True})
    assert reasons == ["user_quit"]


def test_ready_once_attached_and_the_lifecycle_is_ready(make_server: MakeServer) -> None:
    lifecycle = Lifecycle(exit_now=lambda _code: None)
    server = make_server(attach=False, lifecycle=lifecycle)
    server.attach(FakeControl(), INFO)
    assert call(server, "GET", "/health")[1]["ready"] is False  # built, but main has not opened the gate
    assert call(server, "POST", "/register", {})[0] == 503
    lifecycle.mark_ready(lambda _reason: None)
    assert call(server, "GET", "/health")[1]["ready"] is True
    assert call(server, "POST", "/register", {})[0] == 200


# -- /health --------------------------------------------------------------------


def test_health_v2_fields(make_server: MakeServer, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.i18n._active_language", "es")
    status = SessionStatus()
    status.hub.publish_health("down")
    server = make_server(status=status)
    code, body = call(server, "GET", "/health")
    assert code == 200
    assert body["status"] == "ok"
    assert body["api_version"] == 2
    assert isinstance(body["version"], str) and body["version"]
    assert body["instance"] == status.instance
    assert isinstance(body["pid"], int)
    assert isinstance(body["uptime_s"], int)
    assert body["ready"] is True
    assert body["audio"] == "down"
    assert body["lang"] == "es"
    assert (body["platform"], body["trigger"]) == ("wsl2", "socket")
    assert body["flows"] == ["clipboard", "claude_chat"]  # v1 field
    assert body["flow_info"][1] == {"name": "claude_chat", "kind": "claude_chat", "hotkey": "ctrl+alt+a"}
    assert body["tts"] is True
    assert body["auth"] is False


# -- auth -----------------------------------------------------------------------


def test_token_is_required_everywhere_but_health(make_server: MakeServer) -> None:
    reasons: list[ShutdownReason] = []
    server = make_server(token=TOKEN, on_shutdown=reasons.append)
    code, health = call(server, "GET", "/health")
    assert (code, health["auth"]) == (200, True)
    assert call(server, "GET", "/status")[0] == 401
    assert call(server, "POST", "/register", {})[0] == 401  # the v1 script cannot talk to a token daemon
    assert call(server, "POST", "/trigger", {}, token="wrong")[0] == 401
    assert call(server, "POST", "/shutdown", {})[0] == 401
    assert reasons == []
    assert call(server, "GET", "/status", token=TOKEN)[0] == 200
    assert call(server, "POST", "/register", {"name": "companion"}, token=TOKEN)[0] == 200


# -- register / unregister / events -----------------------------------------------


def test_register_grants_leases_and_returns_snapshot_and_cursor(make_server: MakeServer) -> None:
    status = SessionStatus()
    status.set_operation(3, "recording", "clipboard", None, flow_kind="clipboard")
    server = make_server(status=status)
    code, body = call(
        server,
        "POST",
        "/register",
        {
            "name": "VoiceMate companion",
            "version": "1.0",
            "os": "windows",
            "client_key": "install-1",
            "capabilities": ["clipboard", "cues", "telepathy"],
            "lease_s": 5,
        },
    )
    assert code == 200
    assert body["granted"] == ["clipboard", "cues"]
    assert body["lease_s"] == 10  # clamped
    assert body["instance"] == status.instance
    assert body["api_version"] == 2
    assert body["snapshot"]["state"]["state"] == "recording"
    assert body["cursor"] == status.hub.cursor

    # A relaunch with the same key takes the leases over; the old id is gone.
    _, again = call(server, "POST", "/register", {"client_key": "install-1", "capabilities": ["clipboard", "cues"]})
    assert again["granted"] == ["clipboard", "cues"]
    assert call(server, "GET", f"/events?client_id={body['client_id']}&instance={status.instance}&since=0")[0] == 410


@pytest.mark.parametrize(
    "payload",
    [{"capabilities": "cues"}, {"capabilities": [1]}, {"lease_s": "40"}, {"lease_s": True}, {"name": 3}],
)
def test_register_rejects_bad_types(make_server: MakeServer, payload: dict[str, object]) -> None:
    assert call(make_server(), "POST", "/register", payload)[0] == 400


def test_unregister_releases_and_then_the_client_is_gone(make_server: MakeServer) -> None:
    status = SessionStatus()
    server = make_server(status=status)
    _, reg = call(server, "POST", "/register", {"capabilities": ["clipboard"]})
    assert status.hub.holder("clipboard") == reg["client_id"]
    assert call(server, "POST", "/unregister", {"client_id": reg["client_id"]}) == (200, {"ok": True})
    assert status.hub.holder("clipboard") is None
    assert call(server, "POST", "/unregister", {"client_id": reg["client_id"]})[0] == 410
    assert call(server, "POST", "/unregister", {})[0] == 400


def test_events_long_poll_over_http(make_server: MakeServer) -> None:
    status = SessionStatus()
    server = make_server(status=status)
    _, reg = call(server, "POST", "/register", {"capabilities": ["cues"]})
    query = f"/events?client_id={reg['client_id']}&instance={status.instance}&since={reg['cursor']}"

    started = time.monotonic()
    code, empty = call(server, "GET", query + "&wait=0.3")
    assert code == 200
    assert empty["events"] == [] and empty["reset"] is False
    assert empty["leases"] == ["cues"]
    assert 0.25 <= time.monotonic() - started < 3.0

    threading.Timer(0.2, lambda: status.set_operation(1, "recording", "clipboard", None, flow_kind="clipboard")).start()
    started = time.monotonic()
    code, body = call(server, "GET", query + "&wait=20")
    assert time.monotonic() - started < 3.0  # answered on the event, not after 20 s
    (event,) = body["events"]
    assert event["type"] == "state"
    assert event["data"]["state"] == "recording"
    assert event["data"]["needs_cue"] is True
    assert body["cursor"] == event["seq"]


def test_events_validation_and_reset(make_server: MakeServer) -> None:
    status = SessionStatus()
    server = make_server(status=status)
    _, reg = call(server, "POST", "/register", {})
    assert call(server, "GET", "/events")[0] == 400
    assert call(server, "GET", "/events?client_id=ghost&since=0")[0] == 410
    code, body = call(server, "GET", f"/events?client_id={reg['client_id']}&instance=old&since=4&wait=10")
    assert code == 200
    assert body["reset"] is True
    assert [e["type"] for e in body["events"]] == ["snapshot"]


def test_shutdown_event_wakes_a_pending_long_poll(make_server: MakeServer) -> None:
    status = SessionStatus()
    server = make_server(status=status)  # standalone: /shutdown publishes and stops by itself
    _, reg = call(server, "POST", "/register", {})
    query = f"/events?client_id={reg['client_id']}&instance={status.instance}&since={reg['cursor']}&wait=20"
    received: list[dict[str, Any]] = []
    poller = threading.Thread(target=lambda: received.append(call(server, "GET", query, timeout=25)[1]))
    poller.start()
    time.sleep(0.2)
    assert call(server, "POST", "/shutdown", {"reason": "restart"}) == (200, {"ok": True})
    poller.join(timeout=5.0)
    assert received, "the long poll was not woken by the shutdown"
    assert [(e["type"], e["data"]) for e in received[0]["events"]] == [("shutdown", {"reason": "restart"})]
    assert server.wait_stopped(timeout=5.0)


# -- trigger / cancel ---------------------------------------------------------------


def test_trigger_passes_expect_and_answers_the_outcome(make_server: MakeServer) -> None:
    control = FakeControl()
    server = make_server(control=control)
    code, body = call(server, "POST", "/trigger", {"flow": "claude_chat", "client_id": "c1", "expect": "stop"})
    assert code == 200
    assert body == {
        "ok": True,
        "flow": "claude_chat",
        "client_id": "c1",
        "action": "noop",
        "op_seq": 1,
        "state": "recording",
    }
    assert control.toggles == [("claude_chat", "c1", "stop")]
    assert call(server, "POST", "/trigger", {"expect": "maybe"})[0] == 400
    code, missing = call(server, "POST", "/trigger", {"flow": "telepathy"})
    assert code == 404
    assert missing["flows"] == ["clipboard", "claude_chat"]


def test_trigger_failure_is_a_500_and_the_server_keeps_serving(make_server: MakeServer) -> None:
    control = FakeControl()
    control.fail = True
    server = make_server(control=control)
    code, body = call(server, "POST", "/trigger", {"flow": "clipboard"})
    assert code == 500
    assert body["ok"] is False
    assert call(server, "GET", "/health")[0] == 200


def test_cancel(make_server: MakeServer) -> None:
    control = FakeControl()
    server = make_server(control=control)
    assert call(server, "POST", "/cancel", {"op_seq": 4}) == (200, {"action": "cancelled", "state": "idle"})
    assert call(server, "POST", "/cancel", None) == (200, {"action": "cancelled", "state": "idle"})
    assert call(server, "POST", "/cancel", {"op_seq": "4"})[0] == 400
    assert control.cancels == [4, None]


# -- results ------------------------------------------------------------------------


def test_ack_and_results(make_server: MakeServer) -> None:
    status = SessionStatus()
    server = make_server(status=status)
    _, reg = call(server, "POST", "/register", {"capabilities": ["clipboard"]})
    cid = reg["client_id"]
    for n in (1, 2):
        status.publish_result(f"text {n}", op_seq=n, client_id=None, kind="transcript", flow="clipboard", final=True)

    code, unacked = call(server, "GET", f"/results?state=unacked&client_id={cid}&instance={status.instance}")
    assert code == 200
    assert [(r["result_seq"], r["delivery"]) for r in unacked["results"]] == [(1, "pending"), (2, "pending")]
    assert all(isinstance(r["age_s"], float) for r in unacked["results"])

    ack = {
        "client_id": cid,
        "instance": status.instance,
        "acks": [{"result_seq": 1, "status": "delivered", "verified": True}],
    }
    assert call(server, "POST", "/results/ack", ack) == (200, {"ok": True, "unacked": [2]})
    code, recent = call(server, "GET", "/results?state=recent&limit=1")
    assert [(r["result_seq"], r["delivery"]) for r in recent["results"]] == [(2, "pending")]

    assert call(server, "POST", "/results/ack", {**ack, "instance": "old"})[0] == 409
    assert call(server, "POST", "/results/ack", {**ack, "client_id": "ghost"})[0] == 410
    assert call(server, "POST", "/results/ack", {**ack, "acks": [{"result_seq": 1, "status": "lost"}]})[0] == 400
    assert call(server, "GET", "/results?state=everything")[0] == 400
    assert call(server, "GET", "/results?limit=ten")[0] == 400
    assert call(server, "GET", "/results?instance=old")[0] == 409
    assert call(server, "GET", "/results?client_id=ghost")[0] == 410


# -- hardening ------------------------------------------------------------------------


def test_browser_and_rebinding_requests_are_forbidden(make_server: MakeServer) -> None:
    control = FakeControl()
    server = make_server(control=control)
    origin = {"Origin": "https://evil.example"}
    assert call(server, "POST", "/trigger", {}, headers=origin)[0] == 403
    assert call(server, "GET", "/events?client_id=x", headers={"Host": "evil.example"})[0] == 403
    assert control.toggles == []


def test_routes_and_methods(make_server: MakeServer) -> None:
    server = make_server()
    assert call(server, "GET", "/trigger")[0] == 405
    assert call(server, "POST", "/health", {})[0] == 405
    assert call(server, "GET", "/nope")[0] == 404
    assert call(server, "POST", "/register", raw=b"{not json")[0] == 400
