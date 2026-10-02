"""SocketTriggerListener over the daemon's ApiServer: the v1 protocol of the Windows
scripts keeps working (no token): register, trigger, status, result, health, shutdown."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from app.core.session_status import CancelOutcome, SessionStatus, ToggleOutcome
from app.daemon.server import ApiServer, EngineInfo
from app.i18n import active_language
from app.platform.listeners.socket_trigger_listener import SocketTriggerListener
from app.protocol.models import FlowInfo, TriggerExpect

_INFO = EngineInfo(
    platform="wsl2",
    trigger="socket",
    flows=(
        FlowInfo(name="clipboard", kind="clipboard", hotkey="ctrl+alt+v"),
        FlowInfo(name="claude_chat", kind="claude_chat", hotkey="ctrl+alt+a"),
    ),
)


class _Control:
    """Fake session: records the toggles and returns a ToggleOutcome (optionally via a hook)."""

    def __init__(self, status: SessionStatus | None = None) -> None:
        self.event = threading.Event()
        self.calls: list[tuple[str, str | None]] = []
        self.status = status
        self.raise_exc: Exception | None = None

    def toggle(self, handler_id: str, client_id: str | None = None, expect: TriggerExpect = "toggle") -> ToggleOutcome:
        if self.raise_exc is not None:
            raise self.raise_exc
        self.calls.append((handler_id, client_id))
        self.event.set()
        return ToggleOutcome(action="started", op_seq=len(self.calls), state="recording", flow=handler_id)

    def cancel(self, op_seq: int | None = None) -> CancelOutcome:
        return CancelOutcome(action="noop", state="idle")


def _serve(
    status: SessionStatus | None = None, control: _Control | None = None
) -> tuple[SocketTriggerListener, _Control, threading.Thread]:
    store = status or SessionStatus()
    fake = control or _Control(store)
    server = ApiServer(store, info=_INFO, control=fake, port=0)
    server.bind()  # main binds before the model loads; listen() then serves
    listener = SocketTriggerListener(server)
    thread = threading.Thread(target=listener.listen, daemon=True)
    thread.start()
    return listener, fake, thread


@pytest.fixture
def listener() -> Iterator[tuple[SocketTriggerListener, _Control]]:
    instance, control, thread = _serve()
    try:
        yield instance, control
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def _post(port: int, path: str, payload: dict[str, object] | None) -> tuple[int, dict[str, object]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(port: int, path: str) -> tuple[int, dict[str, object]]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _request(
    port: int, method: str, path: str, headers: dict[str, str], body: bytes | None = None
) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_post_trigger_dispatches_named_flow_and_returns_action(
    listener: tuple[SocketTriggerListener, _Control],
) -> None:
    instance, control = listener
    status, body = _post(instance.port, "/trigger", {"flow": "claude_chat"})
    assert status == 200
    assert body["ok"] is True
    assert body["flow"] == "claude_chat"
    assert body["action"] == "started"
    assert body["op_seq"] == 1
    assert control.event.wait(timeout=5.0)
    assert [flow for flow, _cid in control.calls] == ["claude_chat"]


def test_post_trigger_without_body_uses_default_flow(listener: tuple[SocketTriggerListener, _Control]) -> None:
    instance, control = listener
    status, body = _post(instance.port, "/trigger", None)
    assert status == 200
    assert body["flow"] == "clipboard"  # first flow
    assert [flow for flow, _cid in control.calls] == ["clipboard"]


def test_get_trigger_is_rejected(listener: tuple[SocketTriggerListener, _Control]) -> None:
    """POST-only: a GET could be fired by any web page with a plain <img> tag."""
    instance, control = listener
    status, _body = _get(instance.port, "/trigger?flow=claude_chat&client_id=abc123")
    assert status == 405
    assert control.calls == []


@pytest.mark.parametrize("path", ["/trigger", "/shutdown", "/register"])
def test_browser_originated_post_is_forbidden(listener: tuple[SocketTriggerListener, _Control], path: str) -> None:
    """Browsers always send Origin on cross-site POSTs; scripts/WinHTTP don't."""
    instance, control = listener
    status, _body = _request(
        instance.port, "POST", path, {"Origin": "https://evil.example", "Content-Type": "text/plain"}, b"{}"
    )
    assert status == 403
    assert control.calls == []
    assert _get(instance.port, "/health")[0] == 200  # still serving (shutdown refused)


def test_dns_rebinding_host_is_forbidden(listener: tuple[SocketTriggerListener, _Control]) -> None:
    instance, _control = listener
    status, _body = _request(instance.port, "GET", "/result", {"Host": f"evil.example:{instance.port}"})
    assert status == 403
    assert _request(instance.port, "GET", "/health", {"Host": f"localhost:{instance.port}"})[0] == 200


def test_unknown_flow_is_404(listener: tuple[SocketTriggerListener, _Control]) -> None:
    instance, control = listener
    status, body = _post(instance.port, "/trigger", {"flow": "telepathy"})
    assert status == 404
    assert "telepathy" in str(body["error"])
    assert body["flows"] == ["clipboard", "claude_chat"]
    assert control.calls == []


def test_health_reports_v1_fields(
    listener: tuple[SocketTriggerListener, _Control], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.i18n._active_language", "es")  # not the "en" default
    instance, _control = listener
    status, body = _get(instance.port, "/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["flows"] == ["clipboard", "claude_chat"]
    assert isinstance(body["pid"], int)
    assert body["audio"] == "unknown"  # no probe report yet
    assert body["lang"] == active_language() == "es"
    assert isinstance(body["instance"], str)


def test_health_reports_audio_probe_state() -> None:
    store = SessionStatus()
    instance, _control, thread = _serve(store)
    try:
        store.hub.publish_health("down")  # what the AudioServerProbe reports on a change
        assert _get(instance.port, "/health")[1]["audio"] == "down"
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_shutdown_answers_then_stops_the_server() -> None:
    """POST /shutdown is the launcher's clean "Quit": it answers, then listen() returns.
    A request body (e.g. Invoke-RestMethod -Body '{}') is drained, not left unread."""
    instance, _control, thread = _serve()
    status, body = _post(instance.port, "/shutdown", {"reason": "user_quit"})
    assert status == 200
    assert body["ok"] is True
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "listen() should return after /shutdown"


def test_stop_before_listen_does_not_block() -> None:
    store = SessionStatus()
    server = ApiServer(store, info=_INFO, control=_Control(), port=0)
    server.bind()
    listener = SocketTriggerListener(server)
    listener.stop()  # a shutdown that arrives between "ready" and listen()
    thread = threading.Thread(target=listener.listen, daemon=True)
    thread.start()
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_unknown_route_is_404(listener: tuple[SocketTriggerListener, _Control]) -> None:
    instance, _control = listener
    status, _body = _get(instance.port, "/nope")
    assert status == 404


def test_register_status_and_result_roundtrip() -> None:
    """Full flow: register -> trigger -> /trigger returns the action -> the hub publishes
    the result -> /result (default scope=all) delivers the text to Windows."""
    store = SessionStatus()

    class _Stopping(_Control):
        def toggle(
            self, handler_id: str, client_id: str | None = None, expect: TriggerExpect = "toggle"
        ) -> ToggleOutcome:
            store.set_operation(1, "processing", "clipboard", client_id)
            store.record_result("texto do consumidor")
            return ToggleOutcome(action="stopped", op_seq=1, state="processing", flow="clipboard")

    instance, _control, thread = _serve(store, _Stopping(store))
    try:
        status, reg = _post(instance.port, "/register", None)  # v1: empty body
        assert status == 200
        client_id = reg["client_id"]
        assert client_id

        status, body = _post(instance.port, "/trigger", {"flow": "clipboard", "client_id": client_id})
        assert status == 200
        assert body["action"] == "stopped"
        assert body["client_id"] == client_id

        status, st = _get(instance.port, f"/status?client_id={client_id}&scope=all")
        assert status == 200
        assert st["op_seq"] == 1
        assert st["is_yours"] is True
        assert st["result_seq"] == 1

        status, res = _get(instance.port, f"/result?client_id={client_id}&scope=all")
        assert status == 200
        assert res == {
            "seq": 1,
            "text": "texto do consumidor",
            "op_seq": 1,
            "client_id": client_id,
            "error": None,
            "message": "",
            "instance": store.instance,
            "scope": "all",
        }
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_trigger_without_client_id_auto_registers() -> None:
    """Triggering without a client_id emits one and returns it, so the consumer starts using it."""
    store = SessionStatus()
    instance, control, thread = _serve(store)
    try:
        status, body = _post(instance.port, "/trigger", {"flow": "clipboard"})
        assert status == 200
        emitted = body["client_id"]
        assert emitted  # an id was emitted
        assert control.calls == [("clipboard", emitted)]  # and forwarded to the session
        assert store.is_registered(str(emitted))
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_result_scope_mine_filters_by_client() -> None:
    store = SessionStatus()
    alice, bob = store.register(), store.register()
    store.set_operation(1, "processing", "clipboard", alice)
    store.record_result("da alice")
    store.set_operation(2, "processing", "clipboard", bob)
    store.record_result("do bob")

    instance, _control, thread = _serve(store)
    try:
        _, mine = _get(instance.port, f"/result?client_id={alice}&scope=mine")
        assert mine["text"] == "da alice"
        _, glob = _get(instance.port, f"/result?client_id={alice}&scope=all")
        assert glob["text"] == "do bob"  # global = latest across all
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_error_events_reach_the_v1_result_stream() -> None:
    store = SessionStatus()
    store.publish_error(
        "transcription_failed", detail="boom", message="Transcription failed: boom", op_seq=1, client_id=None
    )
    instance, _control, thread = _serve(store)
    try:
        _, res = _get(instance.port, "/result?since=0")
        assert (res["error"], res["message"], res["text"]) == ("transcription_failed", "Transcription failed: boom", "")
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_binding_exception_returns_500_and_keeps_serving() -> None:
    """An exception in the session must not take down the server; the request responds 500."""
    control = _Control()
    control.raise_exc = RuntimeError("toggle exploded")
    instance, _control, thread = _serve(control=control)
    try:
        status, body = _post(instance.port, "/trigger", {"flow": "clipboard"})
        assert status == 500
        assert body["ok"] is False
        # Server stays alive and serving.
        assert _get(instance.port, "/health")[0] == 200
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_port_reports_the_bound_ephemeral_port() -> None:
    instance, _control, thread = _serve()
    try:
        assert instance.port != 0
        deadline = time.monotonic() + 5.0
        while _get(instance.port, "/health")[0] != 200 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        instance.stop()
        thread.join(timeout=5.0)
