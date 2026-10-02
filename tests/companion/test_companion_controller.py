"""The controller against the fake API v2 daemon (real HTTP on loopback, fake desktop)."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from companion_core_fakes import FAST, ControllerKit, FakeBackend, FakeDaemon, FakeDesktopParts, use_english, wait_until

from app.companion import paths
from app.companion.contract import CompanionSettings, CompanionSnapshot, HotkeyBinding, Notification
from app.companion.controller import CompanionControllerImpl, create_controller
from app.companion.settings_store import SettingsStore


@pytest.fixture(autouse=True)
def english(monkeypatch: pytest.MonkeyPatch) -> None:
    use_english(monkeypatch)


@pytest.fixture
def daemon() -> Iterator[FakeDaemon]:
    fake = FakeDaemon().start()
    yield fake
    fake.stop()


@pytest.fixture
def make_controller(tmp_path: Path) -> Iterator[ControllerKit]:
    kit = ControllerKit(tmp_path)
    yield kit
    kit.close()


class Recorder:
    def __init__(self, controller: CompanionControllerImpl) -> None:
        self.snapshots: list[CompanionSnapshot] = []
        self.notifications: list[Notification] = []
        self.threads: set[str] = set()
        self.lock = threading.Lock()
        controller.subscribe(self._snapshot)
        controller.subscribe_notifications(self._notification)

    def _snapshot(self, snapshot: CompanionSnapshot) -> None:
        with self.lock:
            self.snapshots.append(snapshot)
            self.threads.add(threading.current_thread().name)

    def _notification(self, notification: Notification) -> None:
        with self.lock:
            self.notifications.append(notification)

    @property
    def last(self) -> CompanionSnapshot:
        with self.lock:
            return self.snapshots[-1] if self.snapshots else CompanionSnapshot()

    def trays(self) -> list[str]:
        with self.lock:
            return [s.tray_state for s in self.snapshots]

    def titles(self) -> list[str]:
        with self.lock:
            return [n.title for n in self.notifications]


def _connected(controller: CompanionControllerImpl, daemon: FakeDaemon) -> bool:
    return (
        controller.snapshot().supervisor == "healthy"
        and bool(daemon.clients)
        and controller._poller.session() is not None
    )


def test_subscribe_delivers_the_current_snapshot_first(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, _parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    assert wait_until(lambda: recorder.snapshots)
    first = recorder.snapshots[0]
    assert first.tray_state == "stopped" and first.status_text == ""  # nothing localized before start()
    controller.start()
    assert wait_until(lambda: recorder.last.tray_state == "idle")
    revs = [s.rev for s in recorder.snapshots]
    assert revs == sorted(revs) and len(set(revs)) == len(revs)
    assert recorder.threads == {"companion-dispatcher"}


def test_full_dictation_delivers_acks_and_cues(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, parts, backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    assert wait_until(lambda: backend.keepalive)  # attached in WSL mode: keep the distro up
    assert daemon.registrations[0]["capabilities"] == ["clipboard", "cues"]
    assert daemon.registrations[0]["client_key"] == "test-client-key-0001"
    assert wait_until(lambda: parts.hotkeys is not None and parts.hotkeys.held)
    assert parts.hotkeys is not None
    assert parts.hotkeys.held == {"clipboard": "ctrl+alt+shift+f23", "claude_chat": "ctrl+alt+shift+f22"}

    parts.hotkeys.press("clipboard")
    assert wait_until(lambda: len(daemon.triggers) == 1)
    assert daemon.triggers[0]["client_id"] == controller._poller.session().client_id  # type: ignore[union-attr]
    assert wait_until(lambda: recorder.last.tray_state == "recording")
    daemon.go_live()
    assert wait_until(lambda: len(parts.sound.played) == 1)  # start cue only once the mic is live
    controller.toggle("clipboard")
    assert wait_until(lambda: recorder.last.tray_state == "transcribing")
    seq = daemon.publish_result("hello from the fake daemon")["data"]["result_seq"]
    daemon.set_state("idle")
    assert wait_until(lambda: parts.clipboard.texts == ["hello from the fake daemon"])
    assert wait_until(lambda: daemon.ack_status(seq) == "delivered")
    assert daemon.acks[-1][2] == {"result_seq": seq, "status": "delivered", "verified": True}
    assert wait_until(lambda: recorder.last.tray_state == "ready")
    assert recorder.last.status_text.startswith("Copied: hello from")
    assert [p.name.split("-")[0] for p in parts.sound.played] == ["start", "transcribing", "ready"]
    assert wait_until(lambda: recorder.last.tray_state == "idle", timeout=6)
    (item,) = controller.recent_results()
    assert item.record["delivery"] == "delivered" and item.instance == daemon.instance


def test_cancel_drops_the_operation(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    controller.toggle("claude_chat")
    assert wait_until(lambda: recorder.last.tray_state == "recording")
    assert recorder.last.flow == "claude_chat"
    controller.cancel()
    assert wait_until(lambda: daemon.cancels == [{"op_seq": daemon.op_seq}])
    assert wait_until(lambda: recorder.last.tray_state == "idle")
    assert parts.clipboard.texts == [] and parts.sound.played == []


def test_hotkey_while_starting_notifies_and_sends_nothing(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    daemon.ready = False
    controller, _parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: recorder.last.tray_state == "starting")
    controller.toggle("clipboard")
    assert wait_until(lambda: "VoiceMate is starting" in recorder.titles())
    assert daemon.triggers == []
    daemon.ready = True
    assert wait_until(lambda: _connected(controller, daemon))


def test_stale_unacked_results_are_dismissed_and_kept_for_manual_copy(
    make_controller: ControllerKit, daemon: FakeDaemon
) -> None:
    seq = daemon.add_old_unacked("dictated while the app was closed", age_s=45)
    fresh = daemon.add_old_unacked("just now", age_s=2)
    controller, parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: daemon.ack_status(seq) == "dismissed")
    assert wait_until(lambda: daemon.ack_status(fresh) == "delivered")
    assert parts.clipboard.texts == ["just now"]  # stale text never overwrites the clipboard
    assert wait_until(lambda: recorder.last.pending_unacked == 1)
    (pending,) = controller.pending_results()
    assert pending.record["text"] == "dictated while the app was closed"
    assert wait_until(lambda: "Not copied to the clipboard" in recorder.titles())
    controller.copy_result(pending.instance, pending.record["result_seq"])
    assert wait_until(lambda: daemon.ack_status(seq) == "delivered")  # last status wins
    assert parts.clipboard.texts[-1] == "dictated while the app was closed"
    assert wait_until(lambda: recorder.last.pending_unacked == 0)
    assert controller.pending_results() == []


def _stored_texts(path: Path) -> list[str] | None:
    """The texts in pending.json (None while it is missing or unreadable)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return [entry["text"] for entry in data["items"]]


def test_the_not_copied_list_survives_a_restart(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    seq = daemon.add_old_unacked("dictated before the app quit", age_s=45)
    controller, _parts, _backend = make_controller(daemon)
    controller.start()
    assert wait_until(lambda: daemon.ack_status(seq) == "dismissed")  # the daemon forgets it now
    assert wait_until(lambda: _stored_texts(make_controller.pending_path) == ["dictated before the app quit"])
    done = threading.Event()
    controller.quit(done.set)
    assert done.wait(6)

    again, parts, _backend = make_controller(daemon)
    (item,) = again.pending_results()  # back before start(): the UI shows it at once
    assert item.record["text"] == "dictated before the app quit"
    assert (item.instance, item.record["result_seq"]) == (daemon.instance, seq)
    assert again.snapshot().pending_unacked == 1
    recorder = Recorder(again)
    again.start()
    assert wait_until(lambda: _connected(again, daemon))
    again.copy_result(item.instance, item.record["result_seq"])
    assert wait_until(lambda: parts.clipboard.texts == ["dictated before the app quit"])
    assert wait_until(lambda: daemon.ack_status(seq) == "delivered")
    assert wait_until(lambda: recorder.last.pending_unacked == 0)
    assert again.pending_results() == []
    assert wait_until(lambda: not make_controller.pending_path.exists())  # copied: gone from disk too


def test_clear_pending_empties_the_list_and_deletes_the_file(
    make_controller: ControllerKit, daemon: FakeDaemon
) -> None:
    first = daemon.add_old_unacked("old one", age_s=45)
    second = daemon.add_old_unacked("old two", age_s=50)
    controller, parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: recorder.last.pending_unacked == 2)
    assert wait_until(lambda: _stored_texts(make_controller.pending_path) == ["old one", "old two"])
    # The dismissed ACKs leave on the io worker after the list is saved: count them once they landed.
    assert wait_until(lambda: daemon.ack_status(first) == daemon.ack_status(second) == "dismissed")
    acks_before = len(daemon.acks)
    controller.clear_pending([(i.instance, i.record["result_seq"]) for i in controller.pending_results()])
    assert wait_until(lambda: recorder.last.pending_unacked == 0)
    assert controller.pending_results() == []
    assert wait_until(lambda: not make_controller.pending_path.exists())
    threading.Event().wait(0.5)  # a reconciliation round (FAST timings)
    assert controller.pending_results() == [] and len(daemon.acks) == acks_before  # no ACK changes
    assert daemon.ack_status(first) == daemon.ack_status(second) == "dismissed"
    assert parts.clipboard.texts == []


def test_a_broken_pending_file_is_reported_and_never_stops_the_start(
    make_controller: ControllerKit, daemon: FakeDaemon
) -> None:
    good = {
        "instance": "inst-old",
        "result_seq": 3,
        "op_seq": 3,
        "kind": "transcript",
        "flow": "clipboard",
        "text": "still here",
        "final": True,
        "created_ts": 1.0,
        "delivery": "failed",
    }
    bad = [{**good, "kind": []}, {**good, "delivery": {}}, {**good, "created_ts": 10**400}]
    make_controller.pending_path.write_text(json.dumps({"version": 1, "items": [*bad, good]}), encoding="utf-8")
    controller, _parts, _backend = make_controller(daemon)  # an unhashable kind used to raise here
    assert [i.record["text"] for i in controller.pending_results()] == ["still here"]

    make_controller.pending_path.write_text("{ not json", encoding="utf-8")
    broken, _parts, _backend = make_controller(daemon)
    recorder = Recorder(broken)
    assert broken.pending_results() == []
    broken.start()
    assert wait_until(lambda: '"Not copied" list reset' in recorder.titles())
    note = next(n for n in recorder.notifications if n.title == '"Not copied" list reset')
    assert "pending.json.broken-" in note.message and note.level == "warning"


def test_quit_keeps_what_was_still_queued(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    parts.clipboard.fail_next = 1000  # the clipboard stays locked: the job keeps retrying
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    controller.toggle("clipboard")
    assert wait_until(lambda: daemon.triggers)
    seq = daemon.publish_result("dictated just before quitting")["data"]["result_seq"]
    assert wait_until(lambda: parts.clipboard.fail_next < 1000)  # first attempt failed
    assert controller.pending_results() == []  # still retrying, not given up yet
    done = threading.Event()
    controller.quit(done.set)
    assert done.wait(6)
    assert _stored_texts(make_controller.pending_path) == ["dictated just before quitting"]
    assert daemon.ack_status(seq) == "pending"  # never ACKed by the run that quit

    # The daemon kept running (attached): the next run lists it and ACKs it, no late copy.
    again, again_parts, _backend = make_controller(daemon)
    assert [i.record["text"] for i in again.pending_results()] == ["dictated just before quitting"]
    again.start()
    assert wait_until(lambda: daemon.ack_status(seq) == "dismissed")
    assert again_parts.clipboard.texts == []
    assert [i.record["text"] for i in again.pending_results()] == ["dictated just before quitting"]


def test_a_lost_ack_is_sent_again(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    seq = daemon.add_old_unacked("dismissed, but the daemon never heard", age_s=45)
    controller, parts, _backend = make_controller(daemon)
    controller.start()
    assert wait_until(lambda: daemon.ack_status(seq) == "dismissed")
    with daemon.cond:  # as if that ACK had been dropped after its retries
        next(r for r in daemon.results if r.record["result_seq"] == seq).record["delivery"] = "pending"

    def sent() -> int:
        return sum(1 for _client, _instance, ack in daemon.acks if ack["result_seq"] == seq)

    before = sent()
    assert wait_until(lambda: daemon.ack_status(seq) == "dismissed", timeout=3)  # two rounds later
    threading.Event().wait(1.0)  # several more reconciliation rounds (FAST timings)
    settled = sent()
    assert settled - before in (1, 2)  # one resend; a round that overtakes it in transit may double it once
    threading.Event().wait(1.0)
    assert sent() == settled  # no storm once the daemon has it
    assert parts.clipboard.texts == []
    assert [i.record["result_seq"] for i in controller.pending_results()] == [seq]


def test_a_pending_list_that_cannot_be_saved_does_not_break_delivery(
    make_controller: ControllerKit, daemon: FakeDaemon, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    make_controller.pending_path = blocker / "pending.json"
    seq = daemon.add_old_unacked("stale", age_s=45)
    controller, parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: daemon.ack_status(seq) == "dismissed")
    assert wait_until(lambda: recorder.last.pending_unacked == 1)  # still offered, in memory
    fresh = daemon.add_old_unacked("fresh", age_s=1)
    assert wait_until(lambda: daemon.ack_status(fresh) == "delivered", timeout=3)
    controller.copy_result(daemon.instance, seq)
    assert wait_until(lambda: daemon.ack_status(seq) == "delivered")
    assert parts.clipboard.texts == ["fresh", "stale"]
    assert wait_until(lambda: recorder.last.pending_unacked == 0)


def test_reconciliation_picks_up_a_missed_result(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, parts, _backend = make_controller(daemon)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    seq = daemon.add_old_unacked("no event for this one", age_s=1)  # pending, but no event published
    assert wait_until(lambda: daemon.ack_status(seq) == "delivered", timeout=3)
    assert parts.clipboard.texts == ["no event for this one"]


def test_failed_delivery_retries_then_acks_failed(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    parts.clipboard.fail_next = 10
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    controller.toggle("clipboard")
    assert wait_until(lambda: daemon.triggers)
    seq = daemon.publish_result("will not stick")["data"]["result_seq"]
    assert wait_until(lambda: daemon.ack_status(seq) == "failed", timeout=8)
    assert parts.clipboard.fail_next == 6  # 1 attempt + 3 retries
    assert wait_until(lambda: recorder.last.pending_unacked == 1)
    assert "Not copied to the clipboard" in recorder.titles()
    assert any(p.name.startswith("error-") for p in parts.sound.played)


def test_audio_down_twice_restarts_wsl(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    daemon.audio = "down"
    parts = FakeDesktopParts(mics=[2])
    settings = CompanionSettings(engine_mode="wsl2", client_key="test-client-key-0001", notify_level="all")
    controller, _parts, backend = make_controller(daemon, parts=parts, settings=settings)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    assert wait_until(lambda: backend.wsl_shutdowns == 1, timeout=5)  # audio down twice in a row
    assert "Restarting WSL" in recorder.titles()
    assert "restart" in daemon.shutdowns  # POST /shutdown before `wsl --shutdown`
    assert wait_until(lambda: recorder.last.supervisor == "healthy", timeout=5)


def test_mic_error_with_audio_ok_only_notifies(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, _parts, backend = make_controller(daemon, parts=FakeDesktopParts(mics=[1]))
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    controller.toggle("clipboard")
    assert wait_until(lambda: daemon.triggers)
    daemon.emit_error("mic_unavailable", "PortAudio error")
    daemon.set_state("idle")
    assert wait_until(lambda: "Microphone unavailable" in recorder.titles())
    assert wait_until(lambda: recorder.last.tray_state == "warning")
    assert recorder.last.tray_state == "warning"
    assert backend.wsl_shutdowns == 0
    assert "privacy" in recorder.notifications[-1].message


def test_ask_policy_waits_for_the_answer(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    daemon.audio = "down"
    settings = CompanionSettings(engine_mode="wsl2", client_key="test-client-key-0001", wsl_restart_policy="ask")
    controller, _parts, backend = make_controller(daemon, settings=settings, parts=FakeDesktopParts(mics=[1]))
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: recorder.last.pending_wsl_restart, timeout=5)
    assert backend.wsl_shutdowns == 0
    controller.answer_wsl_restart(True)
    assert wait_until(lambda: backend.wsl_shutdowns == 1)
    assert wait_until(lambda: not recorder.last.pending_wsl_restart)


def test_spawn_crash_and_backoff_restart(make_controller: ControllerKit) -> None:
    daemon = FakeDaemon()
    daemon.port = _reserve_port()
    backend = FakeBackend(can_spawn=True, daemon=daemon)
    controller, _parts, _backend = make_controller(port=daemon.port, backend=backend)
    recorder = Recorder(controller)
    try:
        controller.start()
        assert wait_until(lambda: backend.spawned == [("ai-lab/voice-mate", 1)])
        assert wait_until(lambda: recorder.last.supervisor == "healthy")
        backend.crash(code=1)
        assert wait_until(lambda: recorder.last.tray_state == "restarting")
        assert wait_until(lambda: len(backend.spawned) == 2, timeout=5)
        assert wait_until(lambda: recorder.last.supervisor == "healthy")
        assert recorder.last.restarts == 1
        assert "Restarting (attempt 1)" in {s.status_text for s in recorder.snapshots}
    finally:
        daemon.stop()


def test_missing_engine_folder_fails_with_a_settings_notification(make_controller: ControllerKit) -> None:
    backend = FakeBackend(can_spawn=True, engine_dir=None)
    settings = CompanionSettings(engine_mode="wsl2", client_key="test-client-key-0001", engine_dir="")
    controller, _parts, _backend = make_controller(port=_reserve_port(), backend=backend, settings=settings)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: recorder.last.tray_state == "error")
    assert recorder.last.status_text == "Engine folder not found"
    note = next(n for n in recorder.notifications if n.title == "Engine folder not found")
    assert note.action == "open_settings"


def test_detected_engine_folder_is_saved(make_controller: ControllerKit) -> None:
    daemon = FakeDaemon()
    daemon.port = _reserve_port()
    backend = FakeBackend(can_spawn=True, daemon=daemon, engine_dir="workspace/voice-mate")
    settings = CompanionSettings(engine_mode="wsl2", client_key="test-client-key-0001", engine_dir="")
    controller, _parts, _backend = make_controller(port=daemon.port, backend=backend, settings=settings)
    try:
        controller.start()
        assert wait_until(lambda: backend.spawned)
        assert backend.spawned[0][0] == "workspace/voice-mate"
        assert controller.settings().engine_dir == "workspace/voice-mate"
    finally:
        daemon.stop()


def test_a_v1_daemon_is_flagged_outdated_and_never_restarted(make_controller: ControllerKit) -> None:
    """The v1 daemon (no `ready`, no `api_version`) must not cycle through start timeouts."""
    daemon = FakeDaemon(api_version=None).start()
    try:
        assert set(daemon.health()) == {"status", "flows", "pid", "audio", "lang", "instance"}
        backend = FakeBackend(can_spawn=True, daemon=daemon)
        controller, parts, _backend = make_controller(daemon, backend=backend, timings=replace(FAST, grace_s=0.5))
        recorder = Recorder(controller)
        controller.start()
        assert wait_until(lambda: recorder.last.engine_outdated and recorder.last.supervisor == "healthy")
        assert recorder.last.tray_state == "warning"
        assert recorder.last.status_text == "Engine is outdated"
        threading.Event().wait(1.5)  # three times the grace period
        assert "restarting" not in recorder.trays() and recorder.last.supervisor == "healthy"
        assert backend.spawned == [] and backend.stops == []
        assert "Engine is outdated" in recorder.titles()
        assert parts.hotkeys is not None
        assert wait_until(lambda: parts.hotkeys is not None and parts.hotkeys.on_hotkey is not None)
        parts.hotkeys.press("clipboard")
        threading.Event().wait(0.3)
        assert daemon.triggers == []  # refused, with the outdated notice (not "starting")
        assert "VoiceMate is starting" not in recorder.titles()
        assert daemon.registrations == []  # no v1 fallback
    finally:
        daemon.stop()


def test_a_mic_error_then_a_retry(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    """The real daemon goes idle BEFORE `error mic_unavailable`; the next press starts again."""
    controller, parts, backend = make_controller(daemon, parts=FakeDesktopParts(mics=[1]))
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    controller.toggle("clipboard")
    assert wait_until(lambda: recorder.last.tray_state == "recording")
    daemon.fail_microphone()
    assert wait_until(lambda: recorder.last.tray_state == "warning")
    assert "Microphone unavailable" in recorder.titles()
    assert backend.wsl_shutdowns == 0  # audio ok: a Windows-side problem, never a WSL restart
    controller.toggle("clipboard")  # the user plugs the mic back and tries again
    assert wait_until(lambda: recorder.last.tray_state == "recording")
    daemon.go_live()
    controller.toggle("clipboard")
    assert wait_until(lambda: recorder.last.tray_state == "transcribing")
    daemon.publish_result("it works now")
    daemon.set_state("idle")
    assert wait_until(lambda: parts.clipboard.texts == ["it works now"])
    assert wait_until(lambda: recorder.last.tray_state in ("ready", "idle"))
    assert [p.name.split("-")[0] for p in parts.sound.played] == ["error", "start", "transcribing", "ready"]


def test_a_hotkey_taken_at_startup_is_retried_until_it_is_free(
    make_controller: ControllerKit, daemon: FakeDaemon
) -> None:
    parts = FakeDesktopParts()
    assert parts.hotkeys is not None
    hotkeys = parts.hotkeys
    hotkeys.taken = {"ctrl+alt+shift+f23"}  # the old hotkeys script still runs
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: "Hotkey unavailable" in recorder.titles())
    assert wait_until(lambda: hotkeys.held == {"claude_chat": "ctrl+alt+shift+f22"})
    assert wait_until(lambda: hotkeys.missing_calls >= 2)  # retried every hotkey_retry_s
    drops = hotkeys.drops
    # An unrelated change still applies while the chord is taken.
    assert controller.apply_settings(replace(controller.settings(), master_volume=0.5)) == []
    assert wait_until(lambda: hotkeys.missing_calls >= 4)
    assert hotkeys.held == {"claude_chat": "ctrl+alt+shift+f22"}
    hotkeys.taken.clear()  # the user closed the script
    assert wait_until(lambda: hotkeys.held == {"clipboard": "ctrl+alt+shift+f23", "claude_chat": "ctrl+alt+shift+f22"})
    assert hotkeys.drops == drops  # retries never released the chord that was held
    settled = (len(hotkeys.calls), hotkeys.missing_calls)
    threading.Event().wait(1.0)
    assert (len(hotkeys.calls), hotkeys.missing_calls) == settled  # no more retries
    assert recorder.titles().count("Hotkey unavailable") == 1  # notified once


def test_a_broken_settings_file_is_kept_and_reported(tmp_path: Path, daemon: FakeDaemon) -> None:
    path = tmp_path / "companion.toml"
    path.write_text("this = = is not toml", encoding="utf-8")
    parts = FakeDesktopParts()
    backend = FakeBackend(daemon=daemon)
    controller = CompanionControllerImpl(
        SettingsStore(path, platform="win32"),
        desktop_factory=parts.desktop,
        backend_factory=lambda _settings, _logs: backend,
        cues_dir=tmp_path / "cues",
        logs_dir=tmp_path / "logs",
        timings=FAST,
    )
    recorder = Recorder(controller)
    try:
        controller.start()
        assert wait_until(lambda: "Settings reset" in recorder.titles())
        note = next(n for n in recorder.notifications if n.title == "Settings reset")
        assert "companion.toml.broken-" in note.message and note.action == "open_settings"
        (backup,) = tmp_path.glob("companion.toml.broken-*")
        assert backup.read_text(encoding="utf-8") == "this = = is not toml"
    finally:
        done = threading.Event()
        controller.quit(done.set)
        assert done.wait(6)


def test_a_persistent_401_is_reported(make_controller: ControllerKit) -> None:
    daemon = FakeDaemon(token="the-real-token").start()
    try:
        backend = FakeBackend(daemon=daemon, token="an-old-token")
        controller, _parts, _backend = make_controller(daemon, backend=backend)
        recorder = Recorder(controller)
        controller.start()
        assert wait_until(lambda: "Engine access denied" in recorder.titles())
        threading.Event().wait(1.0)
        assert backend.token_reads == 1  # not re-read on every refused request
        assert recorder.titles().count("Engine access denied") == 1
    finally:
        daemon.stop()


def test_restart_engine_in_external_mode_does_not_stop_the_daemon(
    make_controller: ControllerKit, daemon: FakeDaemon
) -> None:
    backend = FakeBackend(can_spawn=False, can_restart_wsl=False, daemon=daemon)
    settings = CompanionSettings(engine_mode="external", client_key="test-client-key-0001")
    controller, _parts, _backend = make_controller(daemon, backend=backend, settings=settings)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    controller.restart_engine()
    assert wait_until(lambda: any(s.supervisor == "restarting" for s in list(recorder.snapshots)))
    assert wait_until(lambda: recorder.last.supervisor == "healthy")
    assert daemon.shutdowns == []  # we could not bring an external daemon back


def test_auth_token_is_used(make_controller: ControllerKit) -> None:
    daemon = FakeDaemon(token="s3cret").start()
    try:
        backend = FakeBackend(daemon=daemon, token="s3cret")
        controller, _parts, _backend = make_controller(daemon, backend=backend)
        controller.start()
        assert wait_until(lambda: _connected(controller, daemon))
        assert backend.token_reads == 1
    finally:
        daemon.stop()


def test_lease_takeover_and_daemon_restart(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, _parts, _backend = make_controller(daemon)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    daemon.expire_leases()
    daemon.set_audio("ok")
    assert wait_until(lambda: len(daemon.registrations) == 2)
    old_instance = daemon.instance
    daemon.restart()
    assert wait_until(lambda: len(daemon.registrations) == 3)
    assert wait_until(lambda: controller.snapshot().instance == daemon.instance != old_instance)


def test_another_clients_quit_stops_supervision(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    daemon.stop_on_shutdown = True
    backend = FakeBackend(can_spawn=True, daemon=daemon)
    controller, _parts, _backend = make_controller(daemon, backend=backend)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    daemon._shutdown({"reason": "user_quit"}, {})  # another client quit the engine
    assert wait_until(lambda: recorder.last.tray_state == "stopped")
    controller.toggle("clipboard")
    assert wait_until(lambda: "VoiceMate is stopped" in recorder.titles())
    assert backend.spawned == []  # not a crash: nothing restarted it
    controller.restart_engine()  # /shutdown to the attached daemon, then spawn our own
    assert wait_until(lambda: backend.spawned, timeout=8)
    assert "restart" in daemon.shutdowns
    assert wait_until(lambda: recorder.last.supervisor == "healthy")


def test_apply_settings(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    assert parts.hotkeys is not None
    parts.hotkeys.taken = {"ctrl+alt+shift+f20"}
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    current = controller.settings()
    assert controller.apply_settings(replace(current, engine_dir='bad"dir')) == [
        "The engine folder cannot contain quotes, $, backticks, backslashes or line breaks."
    ]
    taken = replace(current, hotkeys=(HotkeyBinding("clipboard", "ctrl+alt+shift+f20"),))
    assert controller.apply_settings(taken) == ["Ctrl+Alt+Shift+F20 is already used by another app."]
    assert controller.settings() == current
    changed = replace(
        current, master_volume=0.3, start_at_login=True, hotkeys=(HotkeyBinding("clipboard", "Ctrl+Alt+Shift+F21"),)
    )
    assert controller.apply_settings(changed) == []
    assert controller.settings().master_volume == 0.3
    assert controller.settings().hotkeys == (HotkeyBinding("clipboard", "ctrl+alt+shift+f21"),)
    assert parts.autostart and parts.autostart[-1][0] is True and parts.autostart[-1][1][-1] == "--autostart"
    assert parts.hotkeys.held == {"clipboard": "ctrl+alt+shift+f21"}
    assert controller.check_hotkey("ctrl+alt+shift+f20", "claude_chat") == "in_use"
    assert controller.check_hotkey("ctrl+alt+shift+f21", "claude_chat") == "duplicate"
    assert controller.check_hotkey("ctrl+alt+shift+f21", "clipboard") == "ok"
    assert controller.check_hotkey("shift+a", "clipboard") == "invalid"


def test_apply_settings_restarts_supervision_only_for_engine_changes(
    make_controller: ControllerKit, daemon: FakeDaemon
) -> None:
    settings = CompanionSettings(engine_mode="wsl2", client_key="test-client-key-0001", engine_dir="ai-lab/voice-mate")
    controller, parts, backend = make_controller(daemon, settings=settings)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    # Same engine section (spelled differently): nothing restarts.
    same_engine = replace(controller.settings(), engine_dir=" ~/ai-lab/voice-mate ", notify_level="all")
    assert controller.apply_settings(same_engine) == []
    assert wait_until(lambda: controller.settings().notify_level == "all")
    assert not backend.closed and backend.stops == []
    # A flow may have no hotkey at all.
    only_one = replace(controller.settings(), hotkeys=(HotkeyBinding("claude_chat", "ctrl+alt+shift+f21"),))
    assert controller.apply_settings(only_one) == []
    assert parts.hotkeys is not None
    assert parts.hotkeys.held == {"claude_chat": "ctrl+alt+shift+f21"}
    # A different engine folder: stop what we run, start supervising again.
    assert controller.apply_settings(replace(controller.settings(), engine_dir="other/voice-mate")) == []
    assert wait_until(lambda: backend.closed)
    assert backend.stops == ["restart"]
    assert wait_until(lambda: recorder.last.supervisor == "healthy" and _connected(controller, daemon))
    assert controller.settings().engine_dir == "other/voice-mate"


def test_suspend_hotkeys_is_reflected_in_the_snapshot(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    controller.suspend_hotkeys(True)
    assert wait_until(lambda: recorder.last.hotkeys_suspended)
    assert parts.hotkeys is not None and parts.hotkeys.suspended
    controller.suspend_hotkeys(False)
    assert wait_until(lambda: not recorder.last.hotkeys_suspended)


def test_registry_is_the_source_of_truth_for_start_at_login(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts(autostart_state=True)
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    assert controller.settings().start_at_login is True
    assert controller.apply_settings(replace(controller.settings(), start_at_login=False)) == []
    assert parts.autostart == [(False, parts.autostart[0][1])]


def test_tray_icon_is_promoted_at_startup_until_its_entry_exists(
    make_controller: ControllerKit, daemon: FakeDaemon
) -> None:
    parts = FakeDesktopParts(tray_entry_after=1)  # Explorer creates the entry after the first try
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    assert parts.tray_calls == []  # nothing before start()
    controller.start()
    assert wait_until(lambda: parts.tray_calls == [True, True])
    threading.Event().wait(0.8)
    assert parts.tray_calls == [True, True]  # found: no more tries
    assert parts.tray_only_if_unset == [True, True]  # startup keeps a choice made in Windows


def test_tray_icon_promotion_gives_up_after_the_last_try(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts(tray_entry_after=99)  # Windows 10: never an entry
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    controller.start()
    assert wait_until(lambda: len(parts.tray_calls) == 3)
    threading.Event().wait(0.8)
    assert parts.tray_calls == [True, True, True]


def test_tray_icon_setting_changes_promote_and_demote(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    settings = CompanionSettings(engine_mode="wsl2", client_key="test-client-key-0001", tray_icon_visible=False)
    controller, _parts, _backend = make_controller(daemon, parts=parts, settings=settings)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    assert parts.tray_calls == []  # off at startup: the user's choice in Windows stays as it is
    assert controller.apply_settings(replace(controller.settings(), tray_icon_visible=True)) == []
    assert wait_until(lambda: parts.tray_calls == [True])
    assert controller.apply_settings(replace(controller.settings(), master_volume=0.4)) == []
    assert controller.apply_settings(replace(controller.settings(), tray_icon_visible=False)) == []
    assert wait_until(lambda: parts.tray_calls == [True, False])
    threading.Event().wait(0.8)
    assert parts.tray_calls == [True, False]  # an unrelated change never writes
    assert parts.tray_only_if_unset == [False, False]  # our own checkbox forces the value
    assert controller.settings().tray_icon_visible is False


def test_a_newer_tray_icon_change_cancels_pending_tries(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts(tray_entry_after=99)
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    controller.start()
    assert controller.apply_settings(replace(controller.settings(), tray_icon_visible=False)) == []
    assert wait_until(lambda: parts.tray_calls.count(False) == 4)  # at once, then the three retries
    threading.Event().wait(0.8)  # no fifth try and no late True from the startup promotion
    assert parts.tray_calls[-1] is False and parts.tray_calls.count(False) == 4
    assert True not in parts.tray_calls[parts.tray_calls.index(False) :]


def test_no_tray_icon_setting_on_the_os_is_a_no_op(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts(tray_supported=False)
    controller, _parts, _backend = make_controller(daemon, parts=parts)
    controller.start()
    assert controller.apply_settings(replace(controller.settings(), tray_icon_visible=False)) == []
    assert controller.settings().tray_icon_visible is False
    threading.Event().wait(0.3)
    assert parts.tray_calls == []


def _first_run_controller(tmp_path: Path, daemon: FakeDaemon, parts: FakeDesktopParts) -> CompanionControllerImpl:
    store = SettingsStore(tmp_path / "companion.toml", platform="win32")
    store.save(replace(store.get(), daemon_port=daemon.port))  # never the real daemon's port
    backend = FakeBackend(daemon=daemon)
    return CompanionControllerImpl(
        store,
        desktop_factory=parts.desktop,
        backend_factory=lambda _settings, _logs: backend,
        cues_dir=tmp_path / "cues",
        logs_dir=tmp_path / "logs",
        timings=FAST,
    )


def _stop(controller: CompanionControllerImpl) -> None:
    done = threading.Event()
    controller.quit(done.set)
    assert done.wait(6)


def test_first_run_shows_the_pin_tip_once(tmp_path: Path, daemon: FakeDaemon) -> None:
    parts = FakeDesktopParts()
    controller = _first_run_controller(tmp_path, daemon, parts)
    recorder = Recorder(controller)
    try:
        controller.start()
        assert wait_until(lambda: "Pin VoiceMate to the taskbar" in recorder.titles())
        note = next(n for n in recorder.notifications if n.title == "Pin VoiceMate to the taskbar")
        assert note.level == "info" and note.action == "none"
        assert note.message == "Open Start, search for VoiceMate, right-click it and choose Pin to taskbar."
        assert wait_until(lambda: _connected(controller, daemon))
        assert recorder.titles().count("Pin VoiceMate to the taskbar") == 1
    finally:
        _stop(controller)
    # The second start finds the settings file: no tip.
    second = CompanionControllerImpl(
        SettingsStore(tmp_path / "companion.toml", platform="win32"),
        desktop_factory=FakeDesktopParts().desktop,
        backend_factory=lambda _settings, _logs: FakeBackend(daemon=daemon),
        cues_dir=tmp_path / "cues",
        logs_dir=tmp_path / "logs",
        timings=FAST,
    )
    recorder = Recorder(second)
    try:
        second.start()
        assert wait_until(lambda: _connected(second, daemon))
        assert "Pin VoiceMate to the taskbar" not in recorder.titles()
    finally:
        _stop(second)


def test_no_pin_tip_where_the_os_has_no_taskbar_pinning(tmp_path: Path, daemon: FakeDaemon) -> None:
    controller = _first_run_controller(tmp_path, daemon, FakeDesktopParts(taskbar_pin_tip=False))
    recorder = Recorder(controller)
    try:
        controller.start()
        assert wait_until(lambda: _connected(controller, daemon))
        assert "Pin VoiceMate to the taskbar" not in recorder.titles()
    finally:
        _stop(controller)


def test_no_pin_tip_when_the_settings_file_exists(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, _parts, _backend = make_controller(daemon)
    recorder = Recorder(controller)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    assert "Pin VoiceMate to the taskbar" not in recorder.titles()


def test_engine_owned_hotkeys_in_local_mode(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    settings = CompanionSettings(engine_mode="local", client_key="test-client-key-0001")
    controller, parts, _backend = make_controller(daemon, settings=settings, platform="linux")
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    assert daemon.registrations[0]["capabilities"] == ["cues"]
    assert controller.check_hotkey("ctrl+alt+v", "clipboard") == "engine_owned"
    assert controller.snapshot().hotkeys_owned_by_engine
    assert parts.hotkeys is not None and parts.hotkeys.held == {}


def test_quit_unregisters_stops_and_calls_back(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    controller, parts, backend = make_controller(daemon)
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    done = threading.Event()
    controller.quit(done.set)
    assert done.wait(5)
    assert daemon.unregistered  # leases released at once
    assert backend.stops == ["user_quit"] and backend.closed
    assert parts.hotkeys is not None and parts.hotkeys.stopped
    assert daemon.shutdowns == []  # an attached daemon keeps running
    callers: list[str] = []
    second = threading.Event()

    def again() -> None:
        callers.append(threading.current_thread().name)
        second.set()

    controller.quit(again)  # already done: still called back from a controller thread
    assert second.wait(1)
    assert callers and callers[0] != threading.current_thread().name


def test_quit_is_capped(make_controller: ControllerKit, daemon: FakeDaemon) -> None:
    class SlowBackend(FakeBackend):
        def stop(self, client: object, reason: str) -> None:
            threading.Event().wait(3)

    controller, _parts, _backend = make_controller(
        daemon, backend=SlowBackend(daemon=daemon), timings=replace(FAST, quit_cap_s=0.5)
    )
    controller.start()
    assert wait_until(lambda: _connected(controller, daemon))
    done = threading.Event()
    controller.quit(done.set)
    assert done.wait(1.5)


def test_create_controller_uses_the_settings_file_and_starts_companion_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("LOCALAPPDATA", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(variable, str(tmp_path / "state"))
    path = tmp_path / "c.toml"
    app_logger = logging.getLogger("app")
    handlers_before = list(app_logger.handlers)
    level_before = app_logger.level
    controller = create_controller(path)
    try:
        assert path.is_file()
        assert controller.settings().client_key
        logging.getLogger("app.companion.test").info("hello companion log")
        log_file = paths.companion_log_path()
        assert str(log_file).startswith(str(tmp_path))
        assert str(paths.pending_path()).startswith(str(tmp_path))
        assert "hello companion log" in log_file.read_text(encoding="utf-8")
    finally:
        done = threading.Event()
        controller.quit(done.set)
        assert done.wait(5)
        for handler in app_logger.handlers:
            if handler not in handlers_before:
                app_logger.removeHandler(handler)
                handler.close()
        app_logger.setLevel(level_before)


def _reserve_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
