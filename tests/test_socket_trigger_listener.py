"""SocketTriggerListener: request HTTP real em porta efêmera dispara o callback certo."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from app.platform.listeners.socket_trigger_listener import SocketTriggerListener


class _Recorder:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.count = 0

    def __call__(self) -> None:
        self.count += 1
        self.event.set()


@pytest.fixture
def listener() -> Iterator[tuple[SocketTriggerListener, dict[str, _Recorder]]]:
    callbacks = {"clipboard": _Recorder(), "claude_chat": _Recorder()}
    instance = SocketTriggerListener(dict(callbacks), port=0)  # porta efêmera
    thread = threading.Thread(target=instance.listen, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while instance.port == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert instance.port != 0, "server não subiu a tempo"
    try:
        yield instance, callbacks
    finally:
        instance.stop()
        thread.join(timeout=5.0)


def _post(port: int, path: str, payload: dict[str, str] | None) -> tuple[int, dict[str, object]]:
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


def test_post_trigger_dispatches_named_flow(listener: tuple[SocketTriggerListener, dict[str, _Recorder]]) -> None:
    instance, callbacks = listener
    status, body = _post(instance.port, "/trigger", {"flow": "claude_chat"})
    assert status == 200
    assert body == {"ok": True, "flow": "claude_chat"}
    assert callbacks["claude_chat"].event.wait(timeout=5.0)
    assert callbacks["clipboard"].count == 0


def test_post_trigger_without_body_uses_default_flow(
    listener: tuple[SocketTriggerListener, dict[str, _Recorder]],
) -> None:
    instance, callbacks = listener
    status, body = _post(instance.port, "/trigger", None)
    assert status == 200
    assert body["flow"] == "clipboard"  # primeiro binding
    assert callbacks["clipboard"].event.wait(timeout=5.0)


def test_get_trigger_with_query_flow(listener: tuple[SocketTriggerListener, dict[str, _Recorder]]) -> None:
    instance, callbacks = listener
    status, body = _get(instance.port, "/trigger?flow=claude_chat")
    assert status == 200
    assert body["flow"] == "claude_chat"
    assert callbacks["claude_chat"].event.wait(timeout=5.0)


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
    with pytest.raises(ValueError, match="ao menos um binding"):
        SocketTriggerListener({}, port=0)


def test_callback_exception_is_logged_and_server_keeps_serving(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exceção no callback não pode ser silenciosa (thread daemon morta sem log
    era o sintoma 'trigger não faz nada') nem derrubar o servidor."""
    raised = threading.Event()

    def boom() -> None:
        raised.set()
        raise RuntimeError("toggle explodiu")

    instance = SocketTriggerListener({"clipboard": boom}, port=0)
    thread = threading.Thread(target=instance.listen, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while instance.port == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        status, _body = _post(instance.port, "/trigger", {"flow": "clipboard"})
        assert status == 200  # o request responde mesmo com o callback quebrando
        assert raised.wait(timeout=5.0)

        # O traceback é assíncrono (thread do callback) — aguarda aparecer no stderr.
        err = ""
        deadline = time.monotonic() + 5.0
        while "toggle explodiu" not in err and time.monotonic() < deadline:
            time.sleep(0.05)
            err += capsys.readouterr().err
        assert "toggle explodiu" in err
        assert "Erro no trigger do flow 'clipboard'" in err

        # Servidor continua vivo e atendendo.
        status, body = _get(instance.port, "/health")
        assert status == 200
        assert body["status"] == "ok"
    finally:
        instance.stop()
        thread.join(timeout=5.0)
