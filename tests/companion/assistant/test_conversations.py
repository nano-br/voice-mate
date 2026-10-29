"""VoiceMate-owned conversations (fake ports) and the merged conversation list."""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.companion.assistant.contract import PortListener, PortStatus
from app.companion.assistant.conversations import (
    OWNED_LIMIT,
    REGISTRY_NAME,
    OwnedConversations,
    OwnedError,
    OwnedRecord,
    OwnedRegistry,
    PortSpec,
    list_entries,
    transcript_head,
    valid_model,
)
from app.companion.assistant.own_session import SessionRecord
from app.companion.assistant.session_reader import ConversationInfo, LiveSession


class FakePort:
    def __init__(self, spec: PortSpec) -> None:
        self.spec = spec
        self.sent: list[str] = []
        self.models: list[str] = []
        self.state: PortStatus = PortStatus()
        self.fail_start = False
        self.gate: threading.Event | None = None  # start blocks until it is set
        self.stop_s = 0.0

    def start(self, on_change: PortListener) -> None:
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail_start:
            self.state = PortStatus(state="error", error="cli_missing")
            return
        self.state = PortStatus(state="starting", session_id=self.spec.record.id)

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True

    def status(self) -> PortStatus:
        return self.state

    def stop(self) -> None:
        time.sleep(self.stop_s)
        self.state = PortStatus(state="stopped")

    def set_model(self, model: str) -> bool:
        self.models.append(model)
        return True


class Rig:
    def __init__(self, root: Path) -> None:
        self.ports: list[FakePort] = []
        self.ids = itertools.count()  # thread-safe, unlike a generator
        self.clock = itertools.count(1)
        self.gate: threading.Event | None = None
        self.stop_s = 0.0
        self.owned = OwnedConversations(
            root,
            permission_mode="bypassPermissions",
            port_factory=self.factory,
            clock=lambda: float(next(self.clock)),
            new_id=lambda: f"id-{next(self.ids)}",
        )

    def factory(self, spec: PortSpec) -> FakePort:
        port = FakePort(spec)
        port.gate = self.gate
        port.stop_s = self.stop_s
        self.ports.append(port)
        return port


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_valid_models() -> None:
    assert valid_model("sonnet") and valid_model("claude-haiku-5-5")
    assert not valid_model("gpt-4") and not valid_model("")


def test_start_records_and_runs_the_first_prompt(rig: Rig, tmp_path: Path) -> None:
    conversation_id = rig.owned.start("Research X", "look into X", "haiku", "low")
    assert conversation_id == "id-0"
    spec = rig.ports[0].spec
    assert spec.first_prompt == "look into X"
    assert spec.permission_mode == "bypassPermissions"
    assert spec.record.title == "Research X"
    assert spec.record.model == "haiku" and spec.record.effort == "low"
    assert Path(spec.record.cwd).parent == tmp_path / "work"
    assert Path(spec.record.cwd).name.startswith("research-x-")
    saved = OwnedRegistry(tmp_path / REGISTRY_NAME).load()
    assert [r.id for r in saved] == ["id-0"]
    assert rig.owned.state("id-0") == "busy"
    rig.ports[0].state = replace(rig.ports[0].state, state="ready")
    assert rig.owned.state("id-0") == "idle"


def test_start_validates_its_arguments(rig: Rig, tmp_path: Path) -> None:
    for args in [
        ("", "p"),
        ("t", " "),
        ("t", "p", "gpt"),
        ("t", "p", "sonnet", "max"),
        ("t", "p", "sonnet", "low", str(tmp_path / "nope")),
    ]:
        with pytest.raises(OwnedError) as err:
            rig.owned.start(*args)
        assert err.value.code == "invalid"
    assert rig.ports == []
    assert rig.owned.start("t", "p", cwd=str(tmp_path)) == "id-0"
    assert rig.ports[0].spec.record.cwd == str(tmp_path)


def test_a_failed_start_is_an_error(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    original = rig.factory

    def failing(spec: PortSpec) -> FakePort:
        port = original(spec)
        port.fail_start = True
        return port

    monkeypatch.setattr(rig.owned, "_factory", failing)
    with pytest.raises(OwnedError) as err:
        rig.owned.start("t", "p")
    assert err.value.code == "start_failed"
    assert rig.owned.records() == []


def test_at_most_the_limit_runs(rig: Rig) -> None:
    for n in range(OWNED_LIMIT):
        rig.owned.start(f"c{n}", "p")
    with pytest.raises(OwnedError) as err:
        rig.owned.start("one more", "p")
    assert err.value.code == "limit"
    rig.owned.stop("id-0")
    assert rig.owned.start("one more", "p")


def _in_thread(errors: list[str], call: object) -> threading.Thread:
    def run() -> None:
        try:
            call()  # type: ignore[operator]
        except OwnedError as exc:
            errors.append(exc.code)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_two_starts_at_once_cannot_pass_the_limit_together(rig: Rig) -> None:
    for n in range(OWNED_LIMIT - 1):
        rig.owned.start(f"c{n}", "p")
    rig.gate = threading.Event()  # the next processes take a while to start
    errors: list[str] = []
    threads = [_in_thread(errors, lambda n=n: rig.owned.start(f"late{n}", "p")) for n in range(2)]
    deadline = time.monotonic() + 3
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)
    rig.gate.set()
    for thread in threads:
        thread.join(3)
    assert errors == ["limit"]
    assert sum(1 for r in rig.owned.records() if rig.owned.alive(r.id)) == OWNED_LIMIT


def test_a_start_that_ends_after_stop_all_does_not_outlive_the_quit(rig: Rig) -> None:
    rig.gate = threading.Event()
    errors: list[str] = []
    thread = _in_thread(errors, lambda: rig.owned.start("slow", "p"))
    deadline = time.monotonic() + 3
    while not rig.ports and time.monotonic() < deadline:
        time.sleep(0.01)
    rig.owned.stop_all()
    rig.gate.set()
    thread.join(3)
    assert errors == ["not_running"]
    assert rig.ports[0].state.state == "stopped"
    with pytest.raises(OwnedError) as err:
        rig.owned.start("after", "p")
    assert err.value.code == "not_running"


def test_stop_all_stops_them_at_the_same_time(rig: Rig) -> None:
    rig.stop_s = 0.3
    for n in range(3):
        rig.owned.start(f"c{n}", "p")
    began = time.monotonic()
    rig.owned.stop_all()
    assert time.monotonic() - began < 0.8
    assert all(port.state.state == "stopped" for port in rig.ports)


def test_send_writes_to_a_running_one_and_resumes_a_stopped_one(rig: Rig) -> None:
    rig.owned.start("c", "first")
    rig.owned.send("id-0", "second")
    assert rig.ports[0].sent == ["second"]
    rig.owned.stop("id-0")
    assert rig.owned.record("id-0") is not None and rig.owned.record("id-0").active is False  # type: ignore[union-attr]
    rig.owned.send("id-0", "third")
    assert rig.ports[1].spec.first_prompt == "third"
    assert rig.owned.record("id-0").active is True  # type: ignore[union-attr]
    with pytest.raises(OwnedError) as err:
        rig.owned.send("nope", "x")
    assert err.value.code == "unknown"


def test_set_model_switches_the_running_one_and_is_kept(rig: Rig) -> None:
    rig.owned.start("c", "p")
    rig.owned.set_model("id-0", "opus")
    assert rig.ports[0].models == ["opus"]
    assert rig.owned.record("id-0").model == "opus"  # type: ignore[union-attr]
    with pytest.raises(OwnedError):
        rig.owned.set_model("id-0", "bogus")


def test_resume_all_restarts_the_active_ones_without_a_prompt(tmp_path: Path) -> None:
    records = [
        OwnedRecord(f"r{n}", f"t{n}", str(tmp_path), "sonnet", "medium", active=n != 1, created=float(n))
        for n in range(7)
    ]
    OwnedRegistry(tmp_path / REGISTRY_NAME).save(records)
    rig = Rig(tmp_path)
    resumed = rig.owned.resume_all()
    assert resumed == ["r6", "r5", "r4", "r3"]  # newest active first, at most the limit
    assert all(port.spec.first_prompt is None for port in rig.ports)


def test_stop_all_keeps_them_active_for_the_next_start(rig: Rig, tmp_path: Path) -> None:
    rig.owned.start("c", "p")
    rig.owned.stop_all()
    assert rig.ports[0].state.state == "stopped"
    assert [r.active for r in OwnedRegistry(tmp_path / REGISTRY_NAME).load()] == [True]


def test_the_record_follows_a_new_session_id(rig: Rig) -> None:
    rig.owned.start("c", "p")
    assert rig.ports[0].spec.store.load() is None  # a new conversation has nothing to resume
    rig.owned.stop("id-0")
    rig.owned.send("id-0", "again")  # resumes it
    store = rig.ports[1].spec.store
    assert store.load() == SessionRecord("id-0", "", rig.ports[1].spec.record.cwd)
    store.save(SessionRecord("other", "", "x"))
    assert rig.owned.record("id-0") is None
    assert rig.owned.record("other") is not None
    rig.owned.send("other", "hi")
    assert rig.ports[1].sent == ["hi"]


def test_registry_survives_garbage(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    registry = OwnedRegistry(path)
    assert registry.load() == []
    path.write_text("nope", encoding="utf-8")
    assert registry.load() == []
    path.write_text(
        json.dumps(
            {"conversations": [{"id": "a"}, 3, {"id": "b", "title": "t", "cwd": "c", "model": "m", "effort": "x"}]}
        ),
        encoding="utf-8",
    )
    assert registry.load() == [OwnedRecord("b", "t", "c", "m", "medium", True, 0.0)]


def _transcript(path: Path, entrypoint: str, cwd: str) -> Path:
    lines = [
        {"type": "custom-title", "customTitle": "x"},
        {"type": "user", "entrypoint": entrypoint, "cwd": cwd, "message": {"role": "user", "content": "hi"}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def test_transcript_head(tmp_path: Path) -> None:
    assert transcript_head(_transcript(tmp_path / "a.jsonl", "claude-desktop", "C:\\w")) == ("claude-desktop", "C:\\w")
    assert transcript_head(tmp_path / "missing.jsonl") == ("", "")


def test_list_entries_merges_desktop_and_owned_and_skips_the_rest(rig: Rig, tmp_path: Path) -> None:
    rig.owned.start("Mine", "p")
    desk = _transcript(tmp_path / "d1.jsonl", "claude-desktop", "C:\\proj")
    lab = _transcript(tmp_path / "l1.jsonl", "sdk-py", "C:\\lab")
    central = _transcript(tmp_path / "central.jsonl", "claude-desktop", "C:\\c")
    mine = _transcript(tmp_path / "id-0.jsonl", "sdk-cli", "C:\\mine")
    infos = [
        ConversationInfo("d1", desk, "Desk one", 2_000_000_000.0, "p"),
        ConversationInfo("l1", lab, "Lab", 1_900_000_000.0, "p"),
        ConversationInfo("central", central, "VoiceMate", 1_950_000_000.0, "p"),
        ConversationInfo("id-0", mine, "Mine", 1_800_000_000.0, "p"),
        ConversationInfo("d2", _transcript(tmp_path / "d2.jsonl", "claude-desktop", "C:\\q"), "", 1_700_000_000.0, "p"),
    ]
    live = {"d1": LiveSession("d1", 1, "busy"), "d2": LiveSession("d2", 2, "unknown", "Named live")}
    entries = list_entries(rig.owned, 10, exclude=["central"], lister=lambda _n: infos, sessions=lambda: live)
    assert [(e.id, e.title, e.owner, e.state) for e in entries] == [
        ("d1", "Desk one", "desktop", "busy"),
        ("id-0", "Mine", "voicemate", "busy"),
        ("d2", "Named live", "desktop", "waiting"),
    ]
    assert entries[0].cwd == "C:\\proj"
    assert entries[0].last_activity.startswith("2033-05-18T")
    assert list_entries(rig.owned, 1, lister=lambda _n: infos, sessions=dict)[0].id == "d1"
