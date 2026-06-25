"""SocketTriggerListener: a real HTTP request on an ephemeral port dispatches the right
callback and returns the operation; the register/status/result endpoints expose the state."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from app.core.session_status import SessionStatus, ToggleOutcome
from app.platform.listeners.socket_trigger_listener import SocketTriggerListener


class _Recorder:
    """Fake binding: records the received client_ids and returns a ToggleOutcome."""

    def __init__(self, flow: str = "clipboard", action: str = "started") -> None:
        self.event = threading.Event()
        self.count = 0
        self.client_ids: list[str | None] = []
        self._flow = flow
        self._action = action

    def __call__(self, client_id: str | None) -> ToggleOutcome:
        self.count += 1
        self.client_ids.append(client_id)
        self.event.set()
        return ToggleOutcome(action=self._action, op_seq=self.count, state="recording", flow=self._flow)  # type: ignore[arg-type]


def _serve(instance: SocketTriggerListener) -> threading.Thread:
    thread = threading.Thread(target=instance.listen, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while instance.port == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert instance.port != 0, "server não subiu a tempo"
    return thread


@pytest.fixture
def listener() -> Iterator[tuple[SocketTriggerListener, dict[str, _Recorder]]]:
    callbacks = {"clipboard": _Recorder("clipboard"), "claude_chat": _Recorder("claude_chat")}
    instance = SocketTriggerListener(dict(callbacks), port=0)  # ephemeral port, no status
    thread = _serve(instance)
    try:
        yield instance, callbacks
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


def test_post_trigger_dispatches_named_flow_and_returns_action(
    listener: tuple[SocketTriggerListener, dict[str, _Recorder]],
) -> None:
    instance, callbacks = listener
    status, body = _post(instance.port, "/trigger", {"flow": "claude_chat"})
    assert status == 200
    assert body["ok"] is True
    assert body["flow"] == "claude_chat"
    assert body["action"] == "started"
    assert body["op_seq"] == 1
    assert callbacks["claude_chat"].event.wait(timeout=5.0)
    assert callbacks["clipboard"].count == 0


def test_post_trigger_without_body_uses_default_flow(
    listener: tuple[SocketTriggerListener, dict[str, _Recorder]],
) -> None:
    instance, callbacks = listener
    status, body = _post(instance.port, "/trigger", None)
    assert status == 200
    assert body["flow"] == "clipboard"  # first binding
    assert callbacks["clipboard"].event.wait(timeout=5.0)


def test_get_trigger_with_query_flow_and_client_id(
    listener: tuple[SocketTriggerListener, dict[str, _Recorder]],
) -> None:
    instance, callbacks = listener
    status, body = _get(instance.port, "/trigger?flow=claude_chat&client_id=abc123")
    assert status == 200
    assert body["flow"] == "claude_chat"
    assert body["client_id"] == "abc123"
    assert callbacks["claude_chat"].event.wait(timeout=5.0)
    assert callbacks["claude_chat"].client_ids == ["abc123"]  # the client_id reaches the binding


def test_unknown_flow_is_404(listener: tuple[SocketTriggerListener, dict[str, _Recorder]]) -> None:
    instance, callbacks = listener
    status, body = _post(instance.port, "/trigger", {"flow": "telepathy"})
    assert status == 404
    assert "telepathy" in str(body["error"])
    assert callbacks["clipboard"].count == 0
    assert callbacks["claude_chat"].count == 0


def test_health_reports_flows(listener: tuple[SocketTriggerListener, dict[str, _Recorder]]) -> None:
    instance, _callbacks = listener
    status, body = _get(instance.port, "/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["flows"] == ["clipboard", "claude_chat"]


def test_unknown_route_is_404(listener: tuple[SocketTriggerListener, dict[str, _Recorder]]) -> None:
    instance, _callbacks = listener
    status, _body = _get(instance.port, "/nope")
    assert status == 404


def test_empty_bindings_raise() -> None:
    with pytest.raises(ValueError, match="requires at least one binding"):
        SocketTriggerListener({}, port=0)


def test_register_status_and_result_roundtrip() -> None:
    """Full flow: register → trigger → /trigger returns the action → the hub publishes
    the result → /result (default scope=all) delivers the text to Windows."""
    store = SessionStatus()

    def _binding(client_id: str | None) -> ToggleOutcome:
        store.set_operation(1, "processing", "clipboard", client_id)
        store.record_result("texto do consumidor")
        return ToggleOutcome(action="stopped", op_seq=1, state="processing", flow="clipboard")

    instance = SocketTriggerListener({"clipboard": _binding}, port=0, status=store)
    thread = _serve(instance)
    try:
        status, reg = _post(instance.port, "/register", None)
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
            "scope": "all",
        }
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_trigger_without_client_id_auto_registers() -> None:
    """Triggering without a client_id emits one and returns it, so the consumer starts using it."""
    store = SessionStatus()
    seen: list[str | None] = []

    def _binding(client_id: str | None) -> ToggleOutcome:
        seen.append(client_id)
        return ToggleOutcome(action="started", op_seq=1, state="recording", flow="clipboard")

    instance = SocketTriggerListener({"clipboard": _binding}, port=0, status=store)
    thread = _serve(instance)
    try:
        status, body = _post(instance.port, "/trigger", {"flow": "clipboard"})
        assert status == 200
        emitted = body["client_id"]
        assert emitted  # an id was emitted
        assert seen == [emitted]  # and forwarded to the binding
        assert store.is_registered(str(emitted))
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_status_and_result_404_without_store(
    listener: tuple[SocketTriggerListener, dict[str, _Recorder]],
) -> None:
    instance, _callbacks = listener  # fixture creates it without status
    assert _get(instance.port, "/status")[0] == 404
    assert _get(instance.port, "/result")[0] == 404
    assert _post(instance.port, "/register", None)[0] == 404


def test_result_scope_mine_filters_by_client() -> None:
    store = SessionStatus()
    alice, bob = store.register(), store.register()
    store.set_operation(1, "processing", "clipboard", alice)
    store.record_result("da alice")
    store.set_operation(2, "processing", "clipboard", bob)
    store.record_result("do bob")

    instance = SocketTriggerListener({"clipboard": lambda _c: None}, port=0, status=store)
    thread = _serve(instance)
    try:
        _, mine = _get(instance.port, f"/result?client_id={alice}&scope=mine")
        assert mine["text"] == "da alice"
        _, glob = _get(instance.port, f"/result?client_id={alice}&scope=all")
        assert glob["text"] == "do bob"  # global = latest across all
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def test_binding_exception_returns_500_and_keeps_serving() -> None:
    """An exception in the binding must not take down the server; the request responds 500."""

    def boom(_client_id: str | None) -> ToggleOutcome:
        raise RuntimeError("toggle explodiu")

    instance = SocketTriggerListener({"clipboard": boom}, port=0)
    thread = _serve(instance)
    try:
        status, body = _post(instance.port, "/trigger", {"flow": "clipboard"})
        assert status == 500
        assert body["ok"] is False
        # Server stays alive and serving.
        assert _get(instance.port, "/health")[0] == 200
    finally:
        instance.stop()
        thread.join(timeout=5.0)
