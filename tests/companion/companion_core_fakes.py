"""Test doubles for the companion core: a small API v2 daemon, a fake desktop and backend.

`FakeDaemon` implements the documented API v2 surface (docs/companion-app.md) on a real
loopback port: /health, /register, /unregister, /events (long poll, reset, leases),
/trigger, /cancel, /shutdown, /results, /results/ack, bearer auth and the 503 "not ready"
phase. Tests drive it (state changes, results, errors) and inspect what it received.
"""

from __future__ import annotations

import gettext
import json
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

import app.i18n as i18n_module
from app.companion.contract import CompanionSettings, HotkeyBinding, HotkeyCheck
from app.companion.controller import CompanionControllerImpl, Timings
from app.companion.desktop import Desktop
from app.companion.pending_store import PendingStore
from app.companion.settings_store import SettingsStore, dump_settings
from app.companion.supervisor.backend import ExitCallback, ManagedProcess
from app.protocol.models import ShutdownReason

RING = 512

# Short intervals so the controller tests run in seconds.
FAST = Timings(
    probe_interval_s=0.2,
    tick_s=0.1,
    reconcile_s=0.3,
    mic_poll_s=0.5,
    mic_alert_poll_s=0.2,
    events_wait_s=1,
    quit_cap_s=5.0,
    grace_s=3.0,
    backoff_s=(0.2, 0.4),
    hotkey_retry_s=0.3,
    tray_promote_s=(0.1, 0.3, 0.6),
)


def use_english(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assertions read the English msgids, whatever another test left loaded."""
    monkeypatch.setattr(i18n_module, "_translation", gettext.NullTranslations())


def wait_until(predicate: Callable[[], object], timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


@dataclass
class _Client:
    client_id: str
    key: str
    capabilities: list[str]
    lease_s: int


@dataclass
class _Result:
    record: dict[str, Any]
    created: float  # monotonic, for age_s


@dataclass
class _Entry:
    """A journal entry; per-client flags are rendered from the holders at publish time."""

    event: dict[str, Any]
    cue_holder: str | None = None
    clipboard_holder: str | None = None


_CUED_TYPES = frozenset({"state", "result", "error", "warning"})


@dataclass
class FakeDaemon:
    """API v2 as app/daemon/hub.py serves it: `needs_cue` / `needs_delivery` are rendered
    per client from the LEASE HOLDERS at publish time; a cancelled op publishes nothing
    more; after `shutdown` nothing is appended and /events answers at once.

    `api_version=None` makes /health a v1 payload (no `ready`, no `api_version`)."""

    token: str | None = None
    ready: bool = True
    api_version: int | None = 2
    audio: str = "ok"
    flows: tuple[tuple[str, str, str], ...] = (
        ("clipboard", "clipboard", "ctrl+alt+v"),
        ("claude_chat", "claude_chat", "ctrl+alt+a"),
    )
    stop_on_shutdown: bool = False
    instance: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        self.cond = threading.Condition()
        self.seq = 0
        self.journal: list[_Entry] = []
        self.clients: dict[str, _Client] = {}
        self.leases: dict[str, tuple[str, float]] = {}  # capability -> (client_id, expires)
        self.op_seq = 0
        self.state: dict[str, Any] = self._state_data("idle")
        self.result_seq = 0
        self.results: list[_Result] = []
        self.cancelled: set[int] = set()
        self.shut_down = False
        self.acks: list[tuple[str, str, dict[str, Any]]] = []
        self.triggers: list[dict[str, Any]] = []
        self.cancels: list[dict[str, Any]] = []
        self.shutdowns: list[str] = []
        self.registrations: list[dict[str, Any]] = []
        self.unregistered: list[str] = []
        self.requests: list[tuple[str, str]] = []
        self.started_at = time.monotonic()
        self.server: ThreadingHTTPServer | None = None
        self.port = 0

    # --- lifecycle ---------------------------------------------------------------------

    def start(self, port: int = 0) -> FakeDaemon:
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                daemon._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                daemon._handle(self, "POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, name="fake-daemon", daemon=True).start()
        return self

    def stop(self) -> None:
        server, self.server = self.server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        with self.cond:
            self.cond.notify_all()

    def restart(self) -> None:
        """A new daemon process on the same port: new instance, empty memory."""
        with self.cond:
            self.instance = uuid.uuid4().hex[:12]
            self.seq = 0
            self.journal.clear()
            self.clients.clear()
            self.leases.clear()
            self.op_seq = 0
            self.state = self._state_data("idle")
            self.results.clear()
            self.result_seq = 0
            self.cancelled.clear()
            self.shut_down = False
            self.started_at = time.monotonic()
            self.cond.notify_all()

    # --- state helpers ------------------------------------------------------------------

    def _state_data(self, state: str, **extra: object) -> dict[str, Any]:
        data: dict[str, Any] = {
            "state": state,
            "phase": None,
            "op_seq": self.op_seq,
            "flow": None,
            "flow_kind": None,
            "client_id": None,
            "mic_live": False,
            "needs_cue": False,  # rendered per client
        }
        data.update(extra)
        return data

    def _holder(self, capability: str) -> str | None:
        lease = self.leases.get(capability)
        if lease is None:
            return None
        client_id, expires = lease
        if expires < time.monotonic():
            del self.leases[capability]
            return None
        return client_id

    def _renew(self, client_id: str) -> None:
        client = self.clients.get(client_id)
        if client is None:
            return
        for capability, (holder, _expires) in list(self.leases.items()):
            if holder == client_id:
                self.leases[capability] = (holder, time.monotonic() + client.lease_s)

    def _append(self, kind: str, data: Mapping[str, Any], *, clipboard: bool = False) -> dict[str, Any] | None:
        """Call with `cond` held. Nothing is appended after `shutdown` (but `shutdown` itself)."""
        if self.shut_down:
            return None
        self.seq += 1
        event = {"seq": self.seq, "ts": time.time(), "type": kind, "data": dict(data)}
        self.journal.append(
            _Entry(
                event,
                cue_holder=self._holder("cues") if kind in _CUED_TYPES else None,
                clipboard_holder=self._holder("clipboard") if clipboard else None,
            )
        )
        del self.journal[:-RING]
        if kind == "shutdown":
            self.shut_down = True
        self.cond.notify_all()
        return event

    def _emit(self, kind: str, data: Mapping[str, Any]) -> dict[str, Any] | None:
        with self.cond:
            return self._append(kind, data)

    def _render(self, entry: _Entry, client_id: str) -> dict[str, Any]:
        event = dict(entry.event)
        data = dict(event["data"])
        if event["type"] in _CUED_TYPES:
            data["needs_cue"] = entry.cue_holder is not None and entry.cue_holder == client_id
        if event["type"] == "result":
            data["needs_delivery"] = entry.clipboard_holder is not None and entry.clipboard_holder == client_id
        event["data"] = data
        return event

    def _snapshot(self) -> dict[str, Any]:
        return {"state": dict(self.state), "unacked": self._records("unacked", 50), "audio": self.audio}

    def _records(self, which: str, limit: int) -> list[dict[str, Any]]:
        now = time.monotonic()
        chosen = [r for r in self.results if which != "unacked" or r.record["delivery"] == "pending"]
        out = []
        for result in chosen[-limit:]:
            record = dict(result.record)
            record["age_s"] = round(now - result.created, 3)
            out.append(record)
        return out

    # --- test drivers ----------------------------------------------------------------------

    def set_state(
        self, state: str, *, phase: str | None = None, flow: str | None = None, mic_live: bool = False
    ) -> None:
        with self.cond:
            requester = self.state.get("client_id")
            kind = next((k for name, k, _c in self.flows if name == flow), None)
            self.state = self._state_data(
                state,
                phase=phase if state == "processing" else None,
                flow=flow if state != "idle" else None,
                flow_kind=kind if state != "idle" else None,
                client_id=requester,
                mic_live=mic_live and state == "recording",
            )
            self._append("state", self.state)

    def go_live(self) -> None:
        self.set_state("recording", flow=self.state.get("flow"), mic_live=True)

    def publish_result(
        self, text: str, *, kind: str = "transcript", final: bool = True, flow: str = "clipboard", spoken: bool = False
    ) -> dict[str, Any]:
        """Returns the event as the clipboard-lease holder sees it (empty if dropped)."""
        with self.cond:
            if self.op_seq in self.cancelled or self.shut_down:
                return {}
            holder = self._holder("clipboard")
            self.result_seq += 1
            record = {
                "result_seq": self.result_seq,
                "op_seq": self.op_seq,
                "kind": kind,
                "flow": flow,
                "text": text,
                "final": final,
                "created_ts": time.time(),
                "age_s": 0.0,
                "delivery": "pending" if holder is not None else "daemon",
            }
            self.results.append(_Result(record, time.monotonic()))
            data = {
                "result_seq": self.result_seq,
                "op_seq": self.op_seq,
                "kind": kind,
                "flow": flow,
                "text": text,
                "needs_delivery": False,
                "final": final,
                "spoken": spoken,
                "needs_cue": False,
            }
            self._append("result", data, clipboard=True)
            entry = self.journal[-1]
            return self._render(entry, holder) if holder is not None else dict(entry.event)

    def add_old_unacked(self, text: str, age_s: float) -> int:
        """A pending result published `age_s` ago (e.g. before the companion connected)."""
        with self.cond:
            self.result_seq += 1
            record = {
                "result_seq": self.result_seq,
                "op_seq": self.op_seq,
                "kind": "transcript",
                "flow": "clipboard",
                "text": text,
                "final": True,
                "created_ts": time.time() - age_s,
                "age_s": age_s,
                "delivery": "pending",
            }
            self.results.append(_Result(record, time.monotonic() - age_s))
            return self.result_seq

    def emit_error(self, code: str, detail: str = "") -> None:
        with self.cond:
            if self.op_seq in self.cancelled:
                return
            data = {"code": code, "detail": detail, "message": "localized elsewhere", "op_seq": self.op_seq}
            self._append("error", data)

    def fail_microphone(self, detail: str = "PortAudio error -9996") -> None:
        """What the engine does when the capture stream cannot open: idle first, then the error."""
        self.set_state("idle")
        self.emit_error("mic_unavailable", detail)

    def emit_warning(self, code: str) -> None:
        with self.cond:
            if self.op_seq in self.cancelled:
                return
            self._append("warning", {"code": code, "detail": "", "message": "x", "op_seq": self.op_seq})

    def set_audio(self, audio: str) -> None:
        with self.cond:
            if audio == self.audio:
                return
            self.audio = audio
            self._append("health", {"audio": audio})

    def expire_leases(self) -> None:
        with self.cond:
            self.leases.clear()

    def forget_clients(self) -> None:
        with self.cond:
            self.clients.clear()
            self.leases.clear()

    def ack_status(self, result_seq: int) -> str | None:
        with self.cond:
            for result in self.results:
                if result.record["result_seq"] == result_seq:
                    return str(result.record["delivery"])
        return None

    # --- HTTP ------------------------------------------------------------------------------------

    def _reply(self, handler: BaseHTTPRequestHandler, status: int, body: Mapping[str, Any]) -> None:
        raw = json.dumps(body).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        length = int(handler.headers.get("Content-Length") or 0)
        raw = handler.rfile.read(length) if length else b""
        body: dict[str, Any] = json.loads(raw) if raw else {}
        self.requests.append((method, path))
        if path == "/health" and method == "GET":
            self._reply(handler, 200, self.health())
            return
        if self.token is not None and handler.headers.get("Authorization") != f"Bearer {self.token}":
            self._reply(handler, 401, {"error": "unauthorized"})
            return
        if not self.ready and path not in ("/shutdown", "/unregister"):
            self._reply(handler, 503, {"error": "not ready"})
            return
        routes: dict[tuple[str, str], Callable[[dict[str, Any], dict[str, str]], tuple[int, dict[str, Any]]]] = {
            ("POST", "/register"): self._register,
            ("POST", "/unregister"): self._unregister,
            ("GET", "/events"): self._events,
            ("POST", "/trigger"): self._trigger,
            ("POST", "/cancel"): self._cancel,
            ("POST", "/shutdown"): self._shutdown,
            ("POST", "/results/ack"): self._ack,
            ("GET", "/results"): self._results,
        }
        route = routes.get((method, path))
        if route is None:
            self._reply(handler, 404, {"error": f"unknown route {path}"})
            return
        status, payload = route(body, query)
        self._reply(handler, status, payload)
        if path == "/shutdown" and self.stop_on_shutdown:
            threading.Thread(target=self.stop, daemon=True).start()

    def health(self) -> dict[str, Any]:
        flow_names = [name for name, _k, _c in self.flows]
        if self.api_version is None:
            # What the v1 daemon on port 47821 answers today.
            return {
                "status": "ok",
                "flows": flow_names,
                "pid": 4242,
                "audio": self.audio,
                "lang": "en",
                "instance": self.instance,
            }
        return {
            "status": "ok",
            "api_version": self.api_version,
            "version": "test",
            "instance": self.instance,
            "pid": 4242,
            "uptime_s": int(time.monotonic() - self.started_at),
            "ready": self.ready,
            "audio": self.audio,
            "lang": "en",
            "platform": "wsl2",
            "trigger": "socket",
            "flows": flow_names,
            "flow_info": [{"name": name, "kind": kind, "hotkey": chord} for name, kind, chord in self.flows],
            "tts": False,
            "auth": self.token is not None,
        }

    def _register(self, body: dict[str, Any], _query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        with self.cond:
            self.registrations.append(dict(body))
            key = str(body.get("client_key", ""))
            lease_s = max(10, min(120, int(body.get("lease_s", 40))))
            client_id = uuid.uuid4().hex[:8]
            # Same key: take over that key's leases at once (relaunch after a crash).
            for old_id, old in list(self.clients.items()):
                if key and old.key == key:
                    for capability, (holder, expires) in list(self.leases.items()):
                        if holder == old_id:
                            self.leases[capability] = (client_id, expires)
                    del self.clients[old_id]
            client = _Client(client_id, key, list(body.get("capabilities", [])), lease_s)
            self.clients[client_id] = client
            granted = []
            for capability in client.capabilities:
                current = self._holder(capability)
                if current is None or current == client_id:
                    self.leases[capability] = (client_id, time.monotonic() + lease_s)
                    granted.append(capability)
            return 200, {
                "client_id": client_id,
                "instance": self.instance,
                "api_version": 2,
                "granted": granted,
                "lease_s": lease_s,
                "cursor": self.seq,
                "snapshot": self._snapshot(),
            }

    def _unregister(self, body: dict[str, Any], _query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        with self.cond:
            client_id = str(body.get("client_id", ""))
            if client_id not in self.clients:
                return 410, {"error": "unknown client"}
            del self.clients[client_id]
            for capability, (holder, _expires) in list(self.leases.items()):
                if holder == client_id:
                    del self.leases[capability]
            self.unregistered.append(client_id)
            return 200, {"ok": True}

    def _events(self, _body: dict[str, Any], query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        client_id = query.get("client_id", "")
        since = int(query.get("since", "0"))
        wait = max(0.0, min(25.0, float(query.get("wait", "0"))))
        with self.cond:
            if client_id not in self.clients:
                return 410, {"error": "unknown client"}
            self._renew(client_id)
            oldest = self.journal[0].event["seq"] if self.journal else self.seq + 1
            if query.get("instance") != self.instance or (self.journal and since < oldest - 1):
                snapshot = {"seq": self.seq, "ts": time.time(), "type": "snapshot", "data": self._snapshot()}
                return 200, self._events_body(client_id, [snapshot], reset=True)
            # After `shutdown` the journal is closed: nobody waits.
            self.cond.wait_for(lambda: self.seq > since or self.shut_down or self.server is None, timeout=wait)
            if client_id not in self.clients:
                return 410, {"error": "unknown client"}
            self._renew(client_id)
            events = [self._render(entry, client_id) for entry in self.journal if entry.event["seq"] > since]
            return 200, self._events_body(client_id, events, reset=False)

    def _events_body(self, client_id: str, events: list[dict[str, Any]], *, reset: bool) -> dict[str, Any]:
        leases = [capability for capability in ("clipboard", "cues") if self._holder(capability) == client_id]
        return {"instance": self.instance, "cursor": self.seq, "reset": reset, "events": events, "leases": leases}

    def _trigger(self, body: dict[str, Any], _query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.triggers.append(dict(body))
        flow = str(body.get("flow", "clipboard"))
        client_id = body.get("client_id")
        with self.cond:
            if client_id is not None:
                self._renew(str(client_id))
            current = self.state["state"]
            kind = next((k for name, k, _c in self.flows if name == flow), None)
            if current == "idle":
                self.op_seq += 1
                self.state = self._state_data("recording", flow=flow, flow_kind=kind, client_id=client_id)
                action = "started"
            elif current == "recording":
                self.state = self._state_data(
                    "processing", phase="transcribing", flow=flow, flow_kind=kind, client_id=client_id
                )
                action = "stopped"
            else:
                return 200, {
                    "ok": True,
                    "flow": flow,
                    "client_id": client_id,
                    "action": "noop",
                    "op_seq": self.op_seq,
                    "state": current,
                }
            self._append("state", self.state)
            return 200, {
                "ok": True,
                "flow": flow,
                "client_id": client_id,
                "action": action,
                "op_seq": self.op_seq,
                "state": self.state["state"],
            }

    def _cancel(self, body: dict[str, Any], _query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.cancels.append(dict(body))
        with self.cond:
            was = self.state["state"]
            op_seq = int(body.get("op_seq", self.op_seq))
            if was == "idle":
                return 200, {"action": "noop", "state": "idle"}
            self.cancelled.add(op_seq)  # nothing more is published for it (its idle still is)
            self.state = self._state_data("idle")
            self._append("state", self.state)
            return 200, {"action": "cancelled", "state": "idle"}

    def _shutdown(self, body: dict[str, Any], _query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        reason = str(body.get("reason", "user_quit"))
        self.shutdowns.append(reason)
        self._emit("shutdown", {"reason": reason})
        return 200, {"ok": True}

    def _ack(self, body: dict[str, Any], _query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        with self.cond:
            client_id = str(body.get("client_id", ""))
            if client_id not in self.clients:
                return 410, {"error": "unknown client"}
            if body.get("instance") != self.instance:
                return 409, {"error": "instance mismatch", "instance": self.instance}
            self._renew(client_id)
            for ack in body.get("acks", []):
                self.acks.append((client_id, str(body["instance"]), dict(ack)))
                for result in self.results:
                    if result.record["result_seq"] == ack["result_seq"]:
                        result.record["delivery"] = ack["status"]
            unacked = [r.record["result_seq"] for r in self.results if r.record["delivery"] == "pending"]
            return 200, {"ok": True, "unacked": unacked}

    def _results(self, _body: dict[str, Any], query: dict[str, str]) -> tuple[int, dict[str, Any]]:
        which = query.get("state", "recent")
        limit = int(query.get("limit", "10"))
        with self.cond:
            return 200, {"instance": self.instance, "results": self._records(which, limit)}


# --- desktop -----------------------------------------------------------------------------------


class FakeClipboard:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.fail_next = 0
        self.lock = threading.Lock()

    def deliver(self, text: str, done: Callable[[bool], None]) -> None:
        with self.lock:
            fail = self.fail_next > 0
            if fail:
                self.fail_next -= 1
            else:
                self.texts.append(text)
        threading.Thread(target=done, args=(not fail,), daemon=True).start()


class FakeSound:
    def __init__(self) -> None:
        self.played: list[Path] = []

    def play(self, path: Path) -> None:
        self.played.append(path)

    def stop(self) -> None:
        return None


class FakeHotkeys:
    def __init__(self, taken: set[str] | None = None) -> None:
        self.taken = taken or set()
        self.held: dict[str, str] = {}
        self.desired: dict[str, str] = {}
        self.calls: list[dict[str, str]] = []
        self.drops = 0  # full registrations while something was held
        self.missing_calls = 0
        self.suspended = False
        self.on_hotkey: Callable[[str], None] | None = None
        self.stopped = False

    def start(self, on_hotkey: Callable[[str], None]) -> None:
        self.on_hotkey = on_hotkey

    def stop(self, timeout_s: float = 3.0) -> None:
        self.stopped = True
        self.held.clear()

    def register(self, bindings: Mapping[str, str], *, atomic: bool = False) -> dict[str, HotkeyCheck]:
        self.calls.append(dict(bindings))
        verdicts: dict[str, HotkeyCheck] = {
            flow: "in_use" if chord in self.taken else "ok" for flow, chord in bindings.items()
        }
        if atomic and any(v != "ok" for v in verdicts.values()):
            return verdicts
        if self.held:
            self.drops += 1  # like the Win32 thread: a full register releases everything first
        self.desired = dict(bindings)
        self.held = {flow: chord for flow, chord in bindings.items() if verdicts[flow] == "ok"}
        return verdicts

    def register_missing(self) -> dict[str, HotkeyCheck]:
        self.missing_calls += 1
        verdicts: dict[str, HotkeyCheck] = {}
        for flow, chord in self.desired.items():
            if flow in self.held:
                continue
            verdicts[flow] = "in_use" if chord in self.taken else "ok"
            if verdicts[flow] == "ok":
                self.held[flow] = chord
        return verdicts

    def registered(self) -> dict[str, str]:
        return dict(self.held)

    def check(self, chord: str) -> HotkeyCheck:
        if chord in self.held.values():
            return "ok"
        return "in_use" if chord in self.taken else "ok"

    def suspend(self, suspended: bool) -> None:
        self.suspended = suspended

    def press(self, flow: str) -> None:
        assert self.on_hotkey is not None
        self.on_hotkey(flow)


@dataclass
class FakeDesktopParts:
    clipboard: FakeClipboard = field(default_factory=FakeClipboard)
    sound: FakeSound = field(default_factory=FakeSound)
    hotkeys: FakeHotkeys | None = field(default_factory=FakeHotkeys)
    mics: list[int] = field(default_factory=lambda: [1])
    autostart: list[tuple[bool, list[str]]] = field(default_factory=list)
    autostart_state: bool | None = None
    opened: list[Path] = field(default_factory=list)
    # Tray icon visibility (Windows 11): None = the OS has no such setting.
    tray_supported: bool = True
    tray_entry_after: int = 0  # calls that find no entry before Explorer "creates" it
    tray_calls: list[bool] = field(default_factory=list)
    tray_only_if_unset: list[bool] = field(default_factory=list)
    taskbar_pin_tip: bool = True

    def set_tray_icon_promoted(self, promoted: bool, only_if_unset: bool) -> bool:
        self.tray_only_if_unset.append(only_if_unset)
        self.tray_calls.append(promoted)
        return len(self.tray_calls) > self.tray_entry_after

    def desktop(self) -> Desktop:
        def set_autostart(enabled: bool, command: list[str]) -> None:
            self.autostart.append((enabled, command))
            self.autostart_state = enabled

        return Desktop(
            clipboard=self.clipboard,
            sound=self.sound,
            mic_count=lambda: self.mics[-1],
            set_autostart=set_autostart,
            autostart_enabled=lambda: self.autostart_state,
            open_path=self.opened.append,
            hotkeys=self.hotkeys,
            set_tray_icon_promoted=self.set_tray_icon_promoted if self.tray_supported else None,
            taskbar_pin_tip=self.taskbar_pin_tip,
        )


# --- backend ------------------------------------------------------------------------------------------


@dataclass
class FakeBackend:
    """An engine host whose "process" is the fake daemon (started on spawn when given)."""

    can_spawn: bool = False
    can_restart_wsl: bool = True
    daemon: FakeDaemon | None = None
    token: str | None = None
    engine_dir: str | None = "ai-lab/voice-mate"
    distros: list[str] = field(default_factory=list)
    systemd: bool = False
    spawn_error: bool = False

    def __post_init__(self) -> None:
        self.spawned: list[tuple[str, int]] = []
        self.stops: list[str] = []
        self.wsl_shutdowns = 0
        self.keepalive = False
        self.closed = False
        self.on_exit: ExitCallback | None = None
        self.generation = 0
        self.running = False
        self.token_reads = 0

    def spawn(self, engine_dir: str, generation: int, on_exit: ExitCallback) -> None:
        if self.spawn_error:
            raise OSError("wsl.exe not found")
        self.spawned.append((engine_dir, generation))
        self.on_exit = on_exit
        self.generation = generation
        self.running = True
        if self.daemon is not None and self.daemon.server is None:
            if len(self.spawned) > 1:
                self.daemon.restart()  # a new process: new instance, empty memory
            self.daemon.start(self.daemon.port)

    def crash(self, code: int = 1, address_in_use: bool = False) -> None:
        self.running = False
        if self.daemon is not None:
            self.daemon.stop()
        assert self.on_exit is not None
        self.on_exit(self.generation, code, address_in_use)

    def owned(self) -> ManagedProcess | None:
        return None

    def stop(self, client: object, reason: ShutdownReason) -> None:
        self.stops.append(reason)

    def read_token(self) -> str | None:
        self.token_reads += 1
        return self.token

    def systemd_unit_enabled(self) -> bool:
        return self.systemd

    def detect_engine_dir(self) -> str | None:
        return self.engine_dir

    def ensure_keepalive(self) -> None:
        self.keepalive = True

    def stop_keepalive(self) -> None:
        self.keepalive = False

    def shutdown_wsl(self) -> None:
        self.wsl_shutdowns += 1
        if self.daemon is not None:
            self.daemon.audio = "ok"  # what a WSL restart fixes

    def other_running_distros(self) -> list[str]:
        return list(self.distros)

    def close(self) -> None:
        self.closed = True


# --- controller kit ---------------------------------------------------------------------------------


class ControllerKit:
    """Builds controllers on the fakes (temp settings file, short timings); quits them in `close`."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.built: list[CompanionControllerImpl] = []
        # The Not copied list of every controller built here (a restart reads it back).
        self.pending_path = tmp_path / "pending.json"

    def __call__(
        self,
        daemon: FakeDaemon | None = None,
        *,
        backend: FakeBackend | None = None,
        parts: FakeDesktopParts | None = None,
        settings: CompanionSettings | None = None,
        timings: Timings = FAST,
        port: int | None = None,
        platform: str = "win32",
    ) -> tuple[CompanionControllerImpl, FakeDesktopParts, FakeBackend]:
        """`platform`: which OS the settings are validated for (wsl2 mode needs "win32"),
        so the same controller tests run on Windows and Linux."""
        base = settings or CompanionSettings(
            engine_mode="wsl2",
            client_key="test-client-key-0001",
            hotkeys=(
                HotkeyBinding("clipboard", "ctrl+alt+shift+f23"),
                HotkeyBinding("claude_chat", "ctrl+alt+shift+f22"),
            ),
        )
        if port is not None:
            base = replace(base, daemon_port=port)
        elif daemon is not None:
            base = replace(base, daemon_port=daemon.port)
        path = self.tmp_path / "companion.toml"
        path.write_text(dump_settings(base), encoding="utf-8")
        desktop_parts = parts or FakeDesktopParts()
        fake_backend = backend or FakeBackend(daemon=daemon)
        controller = CompanionControllerImpl(
            SettingsStore(path, platform=platform),
            desktop_factory=desktop_parts.desktop,
            backend_factory=lambda _settings, _logs: fake_backend,
            cues_dir=self.tmp_path / "cues",
            logs_dir=self.tmp_path / "logs",
            timings=timings,
            pending_store=PendingStore(self.pending_path),
        )
        self.built.append(controller)
        return controller, desktop_parts, fake_backend

    def close(self) -> None:
        for controller in self.built:
            done = threading.Event()
            controller.quit(done.set)
            done.wait(6.0)
