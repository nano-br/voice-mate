"""The API v2 against a real RecordingSession + ClipboardHandler (fake mic and STT):
leases move the clipboard and the cues from the daemon to the client and back."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

import numpy as np
import pytest
from numpy.typing import NDArray

from app.core.config import Config
from app.core.recording_session import RecordingSession
from app.core.session_status import SessionStatus
from app.core.transcription_handler import ClipboardHandler
from app.daemon.server import ApiServer, EngineInfo
from app.protocol.models import FlowInfo

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Mic:
    def __init__(self) -> None:
        self.recording = False
        self.stops = 0
        self.audio: NDArray[np.float32] | None = np.zeros(16000, dtype=np.float32)

    def start(
        self, on_failure: Callable[[str], None] | None = None, on_opened: Callable[[], None] | None = None
    ) -> bool:
        if self.recording:
            return False
        self.recording = True
        if on_opened is not None:
            threading.Thread(target=on_opened).start()  # like the real one: off the caller's thread
        return True

    def stop(self) -> NDArray[np.float32] | None:
        if not self.recording:
            return None
        self.recording = False
        self.stops += 1
        return self.audio


class Stt:
    def __init__(self) -> None:
        self.release = threading.Event()
        self.release.set()

    def transcribe(self, audio: NDArray[np.float32]) -> str:
        self.release.wait(timeout=5.0)
        return "hello world"


class Beeps:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def recording_started(self) -> None:
        self.calls.append("start")

    def transcription_complete(self) -> None:
        self.calls.append("ready")

    def timeout_warning(self) -> None:
        self.calls.append("warning")

    def error(self) -> None:
        self.calls.append("error")

    def ai_response_ready(self) -> None:
        self.calls.append("ai_ready")


class Clipboard:
    def __init__(self) -> None:
        self.copied: list[str] = []

    def copy(self, text: str) -> None:
        self.copied.append(text)


class Rig:
    def __init__(self) -> None:
        self.status = SessionStatus()
        self.mic = Mic()
        self.stt = Stt()
        self.beeps = Beeps()
        self.clipboard = Clipboard()
        handler = ClipboardHandler(self.beeps, clipboard=self.clipboard)  # type: ignore[arg-type]
        self.session = RecordingSession(
            recorder=self.mic,  # type: ignore[arg-type]
            transcriber=self.stt,
            audio=self.beeps,  # type: ignore[arg-type]
            config=Config(max_recording_seconds=600),
            handlers={"clipboard": handler},
            status=self.status,
            flow_kinds={"clipboard": "clipboard"},
        )
        info = EngineInfo(
            platform="wsl2",
            trigger="socket",
            flows=(FlowInfo(name="clipboard", kind="clipboard", hotkey="ctrl+alt+v"),),
        )
        self.server = ApiServer(self.status, info=info, control=self.session, port=0)
        self.server.start()

    def call(self, method: str, path: str, payload: object = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else (b"" if method == "POST" else None)
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.port}{path}", data=data, method=method)
        with _NO_PROXY.open(request, timeout=30) as response:
            return dict(json.loads(response.read().decode("utf-8")))

    def events_until(
        self, client_id: str, since: int, predicate: Callable[[dict[str, Any]], bool]
    ) -> tuple[list[dict[str, Any]], int]:
        """Long-poll until an event matches `predicate`; returns every event seen and the cursor."""
        seen: list[dict[str, Any]] = []
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            body = self.call(
                "GET", f"/events?client_id={client_id}&instance={self.status.instance}&since={since}&wait=5"
            )
            seen.extend(body["events"])
            since = body["cursor"]
            if any(predicate(e) for e in body["events"]):
                return seen, since
        raise AssertionError(f"no matching event; saw {seen}")


@pytest.fixture
def rig() -> Iterator[Rig]:
    instance = Rig()
    yield instance
    instance.server.stop(grace=0.2)


def _is_idle(event: dict[str, Any]) -> bool:
    return bool(event["type"] == "state" and event["data"]["state"] == "idle")


def test_companion_with_leases_gets_cues_and_delivery_and_the_daemon_stays_quiet(rig: Rig) -> None:
    reg = rig.call("POST", "/register", {"name": "companion", "client_key": "k", "capabilities": ["clipboard", "cues"]})
    cid = reg["client_id"]

    started = rig.call("POST", "/trigger", {"flow": "clipboard", "client_id": cid, "expect": "start"})
    assert (started["action"], started["state"]) == ("started", "recording")
    again = rig.call("POST", "/trigger", {"flow": "clipboard", "client_id": cid, "expect": "start"})
    assert again["action"] == "noop"  # a repeated "start" never stops the recording
    events, cursor = rig.events_until(cid, reg["cursor"], lambda e: e["type"] == "state" and e["data"]["mic_live"])

    stopped = rig.call("POST", "/trigger", {"flow": "clipboard", "client_id": cid, "expect": "stop"})
    assert (stopped["action"], stopped["state"], stopped["op_seq"]) == ("stopped", "processing", started["op_seq"])
    more, cursor = rig.events_until(cid, cursor, _is_idle)
    events += more

    states = [(e["data"]["state"], e["data"]["phase"], e["data"]["mic_live"]) for e in events if e["type"] == "state"]
    assert states == [
        ("recording", None, False),
        ("recording", None, True),
        ("processing", "transcribing", False),
        ("idle", None, False),
    ]
    assert all(e["data"]["needs_cue"] for e in events if e["type"] in ("state", "result"))
    (result,) = [e["data"] for e in events if e["type"] == "result"]
    assert (result["text"], result["kind"], result["final"], result["needs_delivery"]) == (
        "hello world",
        "transcript",
        True,
        True,
    )

    # The companion holds both leases: the daemon neither beeped nor wrote the clipboard.
    assert rig.beeps.calls == []
    assert rig.clipboard.copied == []
    unacked = rig.call("GET", "/results?state=unacked")["results"]
    assert [(r["result_seq"], r["delivery"]) for r in unacked] == [(result["result_seq"], "pending")]
    ack = rig.call(
        "POST",
        "/results/ack",
        {
            "client_id": cid,
            "instance": rig.status.instance,
            "acks": [{"result_seq": result["result_seq"], "status": "delivered", "verified": True}],
        },
    )
    assert ack == {"ok": True, "unacked": []}

    # Quit: the leases go back and the daemon does its own jobs again.
    rig.call("POST", "/unregister", {"client_id": cid})
    rig.call("POST", "/trigger", {"flow": "clipboard"})
    deadline = time.monotonic() + 5.0
    while "start" not in rig.beeps.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    rig.call("POST", "/trigger", {"flow": "clipboard"})
    while "ready" not in rig.beeps.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert rig.beeps.calls == ["start", "ready"]
    assert rig.clipboard.copied == ["hello world"]
    assert rig.call("GET", "/results?state=recent&limit=1")["results"][0]["delivery"] == "daemon"


def test_cancel_while_processing_publishes_no_result(rig: Rig) -> None:
    reg = rig.call("POST", "/register", {"capabilities": ["clipboard", "cues"]})
    cid = reg["client_id"]
    rig.stt.release.clear()  # hold the transcription
    started = rig.call("POST", "/trigger", {"flow": "clipboard", "client_id": cid})
    rig.events_until(cid, reg["cursor"], lambda e: e["type"] == "state" and e["data"]["mic_live"])
    rig.call("POST", "/trigger", {"flow": "clipboard", "client_id": cid})
    assert rig.call("POST", "/trigger", {"flow": "clipboard", "expect": "stop"})["action"] == "noop"

    cancelled = rig.call("POST", "/cancel", {"op_seq": started["op_seq"]})
    assert cancelled == {"action": "cancelled", "state": "idle"}
    assert rig.call("POST", "/cancel", {})["action"] == "noop"  # nothing left to cancel
    rig.stt.release.set()
    time.sleep(0.3)  # the transcription finishes in the background

    events, _cursor = rig.events_until(cid, reg["cursor"], _is_idle)
    body = rig.call("GET", f"/events?client_id={cid}&instance={rig.status.instance}&since={reg['cursor']}")
    assert [e["type"] for e in body["events"]] == ["state", "state", "state", "state"]  # no result
    assert rig.call("GET", "/results?state=recent")["results"] == []
    assert rig.clipboard.copied == []
    assert events[-1]["data"]["state"] == "idle"


def test_cancel_while_recording_discards_the_audio(rig: Rig) -> None:
    started = rig.call("POST", "/trigger", {"flow": "clipboard"})
    assert rig.call("POST", "/cancel", {"op_seq": started["op_seq"] + 1})["action"] == "noop"  # another op
    assert rig.call("POST", "/cancel", {"op_seq": started["op_seq"]})["action"] == "cancelled"
    assert rig.mic.recording is False
    assert rig.status.hub.state()["state"] == "idle"
    nxt = rig.call("POST", "/trigger", {"flow": "clipboard"})
    assert (nxt["action"], nxt["op_seq"]) == ("started", started["op_seq"] + 1)
