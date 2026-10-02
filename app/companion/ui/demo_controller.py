"""`FakeController`: a `CompanionController` without engine, hotkeys or I/O.

Used by the UI tests and by `python -m app.companion.main --demo`, so the UI can
be built, tested and shown before (and independently of) the real controller.
Like the real one, it publishes from whatever thread changes the state (the
autoplay thread in demo mode), so the demo also exercises the Qt thread hop.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from app.companion.contract import (
    CompanionSettings,
    CompanionSnapshot,
    CueName,
    CueSettings,
    FlowEntry,
    HotkeyBinding,
    HotkeyCheck,
    Notification,
    NotificationListener,
    RecentItem,
    SnapshotListener,
    Unsubscribe,
)
from app.companion.ui.chords import normalize_chord
from app.i18n import _
from app.protocol.models import ResultKind, ResultRecord

DEMO_FLOWS = (
    FlowEntry(name="clipboard", kind="clipboard", engine_chord="ctrl+alt+v"),
    FlowEntry(name="claude_chat", kind="claude_chat", engine_chord="ctrl+alt+a"),
)
DEMO_INSTANCE = "demo-instance"
# Simulated user content (what a person dictated), not UI text.
_SAMPLE_TEXTS = (
    "Remind me to send the quarterly report to the finance team on Friday.",
    "The build is green again after the cache fix.",
    "Let's meet at three to review the onboarding flow.",
)


@dataclass(frozen=True)
class _Step:
    delay: float
    changes: dict[str, Any]
    notification: Notification | None = None


def _record(seq: int, text: str, kind: ResultKind = "transcript", delivery: str = "delivered") -> ResultRecord:
    return ResultRecord(
        result_seq=seq,
        op_seq=seq,
        kind=kind,
        flow="claude_chat" if kind == "ai_response" else "clipboard",
        text=text,
        final=True,
        created_ts=0.0,
        age_s=0.0,
        delivery="pending" if delivery == "pending" else "delivered",
    )


class FakeController:
    """Records every command in `calls`; tests steer it with `publish`, `notify`,
    `apply_errors` and `hotkey_results`. `autoplay=True` cycles through every tray
    state on a background thread (demo mode)."""

    def __init__(
        self,
        *,
        settings: CompanionSettings | None = None,
        flows: tuple[FlowEntry, ...] = DEMO_FLOWS,
        autoplay: bool = False,
        step_scale: float = 1.0,
    ) -> None:
        self._lock = threading.Lock()
        self._settings = settings or CompanionSettings(
            client_key="demo", wsl_distro="ai-lab", engine_dir="ai-lab/voice-mate"
        )
        self._snapshot = CompanionSnapshot(flows=flows, instance=DEMO_INSTANCE)
        self._listeners: list[SnapshotListener] = []
        self._notification_listeners: list[NotificationListener] = []
        self._recent: list[RecentItem] = [
            RecentItem(DEMO_INSTANCE, _record(seq, text)) for seq, text in enumerate(reversed(_SAMPLE_TEXTS), 1)
        ][::-1]
        self._pending: list[RecentItem] = []
        self._next_seq = len(self._recent) + 1
        self._autoplay = autoplay
        self._step_scale = step_scale
        self._stop = threading.Event()
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.apply_errors: list[str] = []
        self.hotkey_results: dict[str, HotkeyCheck] = {}
        self.quit_delay = 0.0

    # ----------------------------------------------------------------- test hooks

    def publish(self, **changes: object) -> CompanionSnapshot:
        """Publish a new snapshot with `changes` (rev + 1) to every listener, in this thread."""
        with self._lock:
            self._snapshot = replace(self._snapshot, rev=self._snapshot.rev + 1, **changes)  # type: ignore[arg-type]
            snapshot = self._snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            listener(snapshot)
        return snapshot

    def notify(self, notification: Notification) -> None:
        with self._lock:
            listeners = list(self._notification_listeners)
        for listener in listeners:
            listener(notification)

    def add_pending(self, text: str) -> RecentItem:
        with self._lock:
            item = RecentItem(DEMO_INSTANCE, _record(self._next_seq, text, delivery="pending"))
            self._next_seq += 1
            self._pending.append(item)
            count = len(self._pending)
        self.publish(pending_unacked=count)
        return item

    def _call(self, name: str, *args: object) -> None:
        with self._lock:
            self.calls.append((name, args))

    def called(self, name: str) -> list[tuple[object, ...]]:
        with self._lock:
            return [args for call, args in self.calls if call == name]

    # ----------------------------------------------------------------- protocol

    def start(self) -> None:
        self._call("start")
        if self._autoplay:
            self.notify(
                Notification(
                    "info",
                    _("VoiceMate demo"),
                    _("Nothing is recorded: the states change by themselves."),
                    "show_status",
                )
            )
            threading.Thread(target=self._autoplay_loop, name="fake-controller-demo", daemon=True).start()
        else:
            self.publish(tray_state="idle", supervisor="healthy", engine_ready=True, audio="ok", mic_count=1)

    def snapshot(self) -> CompanionSnapshot:
        with self._lock:
            return self._snapshot

    def subscribe(self, listener: SnapshotListener) -> Unsubscribe:
        with self._lock:
            self._listeners.append(listener)
            snapshot = self._snapshot
        listener(snapshot)
        return lambda: self._remove(self._listeners, listener)

    def subscribe_notifications(self, listener: NotificationListener) -> Unsubscribe:
        with self._lock:
            self._notification_listeners.append(listener)
        return lambda: self._remove(self._notification_listeners, listener)

    def _remove[L](self, listeners: list[L], listener: L) -> None:
        with self._lock:
            if listener in listeners:
                listeners.remove(listener)

    def toggle(self, flow: str) -> None:
        self._call("toggle", flow)
        state = self.snapshot().tray_state
        if state == "recording":
            self.publish(tray_state="transcribing", flow=flow, recording_since=None)
            self._later(1.5, lambda: self._finish(flow))
        elif state in ("idle", "ready", "warning"):
            self.publish(tray_state="recording", flow=flow, recording_since=time.time())

    def cancel(self) -> None:
        self._call("cancel")
        self.publish(tray_state="idle", flow=None, recording_since=None)

    def restart_engine(self) -> None:
        self._call("restart_engine")
        self.publish(tray_state="restarting", supervisor="restarting", engine_ready=False, restarts=0)
        self._later(2.0, self._healthy)

    def restart_wsl(self) -> None:
        self._call("restart_wsl")
        self.publish(tray_state="restarting", supervisor="restarting", engine_ready=False, pending_wsl_restart=False)
        self._later(3.0, self._healthy)

    def answer_wsl_restart(self, approved: bool) -> None:
        self._call("answer_wsl_restart", approved)
        self.publish(pending_wsl_restart=False)
        if approved:
            self.restart_wsl()

    def quit(self, on_done: Callable[[], None]) -> None:
        self._call("quit")
        self._stop.set()

        def finish() -> None:
            if self.quit_delay:
                time.sleep(self.quit_delay)
            self.publish(tray_state="stopped", supervisor="stopped", engine_ready=False)
            on_done()

        threading.Thread(target=finish, name="fake-controller-quit", daemon=True).start()

    def settings(self) -> CompanionSettings:
        with self._lock:
            return self._settings

    def apply_settings(self, settings: CompanionSettings) -> list[str]:
        """Like the core: invalid chords (an empty one included) are refused, and what is
        saved is normalized (canonical chords, no "~/" in the engine folder)."""
        self._call("apply_settings", settings)
        if self.apply_errors:
            return list(self.apply_errors)
        invalid = [binding.chord for binding in settings.hotkeys if normalize_chord(binding.chord) is None]
        if invalid:
            return [_("{hotkey} is not a valid hotkey.").format(hotkey=chord) for chord in invalid]
        hotkeys = tuple(HotkeyBinding(b.flow, normalize_chord(b.chord) or b.chord) for b in settings.hotkeys)
        engine_dir = settings.engine_dir.strip().removeprefix("~/")
        self.set_settings(replace(settings, hotkeys=hotkeys, engine_dir=engine_dir))
        return []

    def set_settings(self, settings: CompanionSettings) -> None:
        """Test hook: the core changing its settings on its own (e.g. a detected engine_dir)."""
        with self._lock:
            self._settings = settings
        self.publish()

    def check_hotkey(self, chord: str, flow: str) -> HotkeyCheck:
        self._call("check_hotkey", chord, flow)
        if chord in self.hotkey_results:
            return self.hotkey_results[chord]
        if self.snapshot().hotkeys_owned_by_engine:
            return "engine_owned"
        normalized = normalize_chord(chord)
        if normalized is None:
            return "invalid"
        for binding in self.settings().hotkeys:
            if binding.flow != flow and normalize_chord(binding.chord) == normalized:
                return "duplicate"
        return "ok"

    def suspend_hotkeys(self, suspended: bool) -> None:
        self._call("suspend_hotkeys", suspended)
        self.publish(hotkeys_suspended=suspended)

    def preview_cue(self, cue: CueName, settings: CueSettings, master_volume: float) -> None:
        self._call("preview_cue", cue, settings, master_volume)

    def recent_results(self) -> list[RecentItem]:
        with self._lock:
            return list(self._recent)

    def pending_results(self) -> list[RecentItem]:
        with self._lock:
            return list(self._pending)

    def copy_result(self, instance: str, result_seq: int) -> None:
        self._call("copy_result", instance, result_seq)
        with self._lock:
            self._pending = [
                item for item in self._pending if (item.instance, item.record["result_seq"]) != (instance, result_seq)
            ]
            count = len(self._pending)
        self.publish(pending_unacked=count)

    def open_logs(self) -> None:
        self._call("open_logs")

    # ----------------------------------------------------------------- demo mode

    def _later(self, delay: float, action: Callable[[], None]) -> None:
        if not self._autoplay:
            return
        timer = threading.Timer(delay * self._step_scale, action)
        timer.daemon = True
        timer.start()

    def _healthy(self) -> None:
        self.publish(tray_state="idle", supervisor="healthy", engine_ready=True, audio="ok", mic_count=1)

    def _finish(self, flow: str) -> None:
        kind: ResultKind = "ai_response" if flow == "claude_chat" else "transcript"
        text = _SAMPLE_TEXTS[self._next_seq % len(_SAMPLE_TEXTS)]
        with self._lock:
            self._recent.insert(0, RecentItem(DEMO_INSTANCE, _record(self._next_seq, text, kind)))
            del self._recent[10:]
            self._next_seq += 1
        self.publish(tray_state="ready", last_text_snippet=text, flow=None)
        self._later(3.0, self._back_to_idle)

    def _back_to_idle(self) -> None:
        self.publish(tray_state="idle")

    def _script(self) -> list[_Step]:
        claude = "claude_chat"
        return [
            _Step(0.0, {"tray_state": "starting", "supervisor": "starting"}),
            _Step(2.5, {"tray_state": "idle", "supervisor": "healthy", "engine_ready": True, "audio": "ok"}),
            _Step(3.0, {"tray_state": "recording", "flow": "clipboard", "recording_since": 0.0}),
            _Step(4.0, {"tray_state": "transcribing", "recording_since": None}),
            _Step(2.0, {"tray_state": "ready", "last_text_snippet": _SAMPLE_TEXTS[0], "flow": None}),
            _Step(3.0, {"tray_state": "idle"}),
            _Step(2.0, {"tray_state": "recording", "flow": claude, "recording_since": 0.0}),
            _Step(3.0, {"tray_state": "transcribing", "recording_since": None}),
            _Step(1.5, {"tray_state": "thinking"}),
            _Step(2.5, {"tray_state": "speaking"}),
            _Step(2.5, {"tray_state": "ready", "last_text_snippet": _SAMPLE_TEXTS[2], "flow": None}),
            _Step(
                3.0,
                {"tray_state": "warning", "audio": "down", "pending_wsl_restart": True},
                Notification(
                    "warning",
                    _("WSL audio stopped"),
                    _("Restart WSL to bring the microphone back."),
                    "show_status",
                ),
            ),
            _Step(5.0, {"tray_state": "restarting", "supervisor": "restarting", "engine_ready": False, "restarts": 2}),
            _Step(3.0, {"tray_state": "error", "supervisor": "failed", "pending_wsl_restart": False}),
            _Step(4.0, {"tray_state": "idle", "supervisor": "healthy", "engine_ready": True, "audio": "ok"}),
        ]

    def _autoplay_loop(self) -> None:
        self.add_pending(_SAMPLE_TEXTS[1])
        while not self._stop.is_set():
            for step in self._script():
                if self._stop.wait(step.delay * self._step_scale):
                    return
                changes = dict(step.changes)
                if changes.get("recording_since") == 0.0:
                    changes["recording_since"] = time.time()
                self.publish(**changes)
                if step.notification is not None:
                    self.notify(step.notification)
