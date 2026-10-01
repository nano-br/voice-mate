"""EventHub: state/phase events, per-client cue and delivery flags decided at publish time,
cancelled operations, register snapshot + cursor, resets, ACK, shutdown."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from app.daemon.hub import DROPPED, EventHub, InstanceMismatch, Publication, UnknownClient
from app.protocol.models import Event, EventsResponse


def _types(response: EventsResponse) -> list[str]:
    return [event["type"] for event in response["events"]]


def _data(event: Event) -> dict[str, Any]:
    return dict(event["data"])


def _register(hub: EventHub, *caps: str, key: str = "") -> tuple[str, int]:
    response = hub.register(name="test", client_key=key, capabilities=caps)  # type: ignore[arg-type]
    return response["client_id"], response["cursor"]


def _poll(hub: EventHub, client_id: str, since: int, wait: float = 0.0) -> EventsResponse:
    return hub.poll(client_id, instance=hub.instance, since=since, wait=wait)


def test_state_event_on_every_state_or_phase_change_and_none_for_repeats() -> None:
    hub = EventHub()
    cid, cursor = _register(hub)
    hub.set_state(1, "recording", flow="clipboard", flow_kind="clipboard", client_id=cid)
    hub.set_mic_live(1)
    hub.set_state(1, "processing", flow="claude_chat", flow_kind="claude_chat", client_id=cid, phase="transcribing")
    hub.set_phase(1, "transcribing")  # no change: no event
    hub.set_phase(1, "thinking")
    hub.set_phase(1, "speaking")
    hub.set_idle(1)
    hub.set_idle(1)  # already idle: no event

    events = _poll(hub, cid, cursor)["events"]
    states = [(_data(e)["state"], _data(e)["phase"], _data(e)["mic_live"], _data(e)["flow"]) for e in events]
    assert states == [
        ("recording", None, False, "clipboard"),  # the flow that STARTED it
        ("recording", None, True, "clipboard"),  # the mic is live: the start cue moment
        ("processing", "transcribing", False, "claude_chat"),  # the destination flow
        ("processing", "thinking", False, "claude_chat"),
        ("processing", "speaking", False, "claude_chat"),
        ("idle", None, False, None),
    ]
    assert _data(events[2])["flow_kind"] == "claude_chat"
    assert all(_data(e)["op_seq"] == 1 and _data(e)["client_id"] == cid for e in events)


def test_stale_operations_cannot_change_the_state() -> None:
    hub = EventHub()
    hub.set_state(2, "recording", flow="clipboard", flow_kind="clipboard", client_id=None)
    hub.set_phase(1, "thinking")  # an older operation still finishing
    hub.set_idle(1)
    assert hub.set_mic_live(1) is False
    state = hub.state()
    assert (state["op_seq"], state["state"]) == (2, "recording")


def test_mic_live_only_once_and_only_while_recording() -> None:
    hub = EventHub()
    hub.set_state(1, "recording", flow="clipboard", flow_kind="clipboard", client_id=None)
    assert hub.set_mic_live(1) is True  # nobody holds `cues`: the daemon beeps
    assert hub.set_mic_live(1) is False  # already live
    hub.set_state(1, "processing", flow="clipboard", flow_kind="clipboard", client_id=None, phase="transcribing")
    assert hub.state()["mic_live"] is False
    assert hub.set_mic_live(1) is False


def test_cue_lease_moves_the_cues_to_the_holder_only() -> None:
    hub = EventHub()
    holder, cursor = _register(hub, "cues")
    other, _ = _register(hub)
    hub.set_state(1, "recording", flow="clipboard", flow_kind="clipboard", client_id=None)
    assert hub.set_mic_live(1) is False  # the holder plays the start cue, not the daemon
    warning = hub.publish_warning("time_limit_soon", detail="", message="", op_seq=1)
    assert warning.play_cue is False

    for_holder = _poll(hub, holder, cursor)["events"]
    for_other = _poll(hub, other, cursor)["events"]
    assert [_data(e)["needs_cue"] for e in for_holder] == [True, True, True]
    assert [_data(e)["needs_cue"] for e in for_other] == [False, False, False]


def test_result_with_clipboard_lease_is_pending_and_only_the_holder_must_deliver() -> None:
    hub = EventHub()
    holder, cursor = _register(hub, "clipboard")
    other, _ = _register(hub)
    publication = hub.publish_result("hello", op_seq=3, kind="transcript", flow="clipboard", final=True)
    assert publication == Publication(published=True, write_clipboard=False, play_cue=True)

    (event,) = _poll(hub, holder, cursor)["events"]
    data = _data(event)
    assert data["needs_delivery"] is True
    assert data["needs_cue"] is False  # nobody holds `cues`: the daemon beeped
    assert (data["text"], data["final"], data["spoken"], data["kind"]) == ("hello", True, False, "transcript")
    assert _data(_poll(hub, other, cursor)["events"][0])["needs_delivery"] is False
    (record,) = hub.results("unacked", 10)["results"]
    assert record["delivery"] == "pending"


def test_without_holders_the_daemon_does_every_job_and_results_are_daemon_delivered() -> None:
    hub = EventHub()
    publication = hub.publish_result("hi", op_seq=1, kind="transcript", flow="clipboard", final=True)
    assert publication == Publication(published=True, write_clipboard=True, play_cue=True)
    assert hub.results("unacked", 10)["results"] == []
    assert hub.results("recent", 10)["results"][0]["delivery"] == "daemon"


def test_unregister_gives_the_jobs_back_to_the_daemon() -> None:
    hub = EventHub()
    cid, _ = _register(hub, "clipboard", "cues")
    hub.unregister(cid)
    assert hub.publish_result("x", op_seq=1, kind="transcript", flow=None, final=True) == Publication(True, True, True)
    with pytest.raises(UnknownClient):
        hub.unregister(cid)


def test_cancelled_operation_publishes_nothing_more() -> None:
    hub = EventHub()
    cid, cursor = _register(hub)
    hub.cancel(5)
    assert hub.is_cancelled(5)
    assert hub.publish_result("late", op_seq=5, kind="transcript", flow=None, final=True) == DROPPED
    assert hub.publish_error("transcription_failed", detail="", message="", op_seq=5) == DROPPED
    assert hub.publish_warning("no_speech", detail="", message="", op_seq=5) == DROPPED
    assert _poll(hub, cid, cursor)["events"] == []
    assert hub.results("recent", 10)["results"] == []


def test_register_returns_the_snapshot_at_its_cursor() -> None:
    """An operation already in progress is visible at once, and polling from the cursor
    yields only what happened after the snapshot."""
    hub = EventHub()
    hub.set_state(4, "recording", flow="claude_chat", flow_kind="claude_chat", client_id="x")
    hub.publish_health("down")
    response = hub.register(name="companion", capabilities=["clipboard"])
    snapshot = response["snapshot"]
    assert snapshot["state"]["state"] == "recording"
    assert snapshot["state"]["flow"] == "claude_chat"
    assert snapshot["state"]["needs_cue"] is False  # never a cue when rendering a snapshot
    assert snapshot["audio"] == "down"
    assert response["granted"] == ["clipboard"]
    assert response["api_version"] == 2
    assert response["instance"] == hub.instance
    assert _poll(hub, response["client_id"], response["cursor"])["events"] == []


def test_snapshot_lists_unacked_results() -> None:
    hub = EventHub()
    _register(hub, "clipboard")
    hub.publish_result("pending text", op_seq=1, kind="transcript", flow="clipboard", final=True)
    late = hub.register(name="late")
    assert [r["text"] for r in late["snapshot"]["unacked"]] == ["pending text"]


@pytest.mark.parametrize("since", [None, 999])
def test_reset_with_snapshot_on_unknown_cursor(since: int | None) -> None:
    hub = EventHub()
    cid, _ = _register(hub)
    hub.set_state(1, "recording", flow="clipboard", flow_kind="clipboard", client_id=None)
    response = hub.poll(cid, instance=hub.instance, since=since, wait=5.0)
    assert response["reset"] is True
    assert _types(response) == ["snapshot"]
    assert response["cursor"] == response["events"][0]["seq"] == hub.cursor


def test_reset_on_instance_mismatch_and_stale_cursor() -> None:
    hub = EventHub(journal_capacity=2)
    cid, _ = _register(hub)
    assert hub.poll(cid, instance="other", since=0, wait=0)["reset"] is True
    for n in range(4):
        hub.publish_health("ok" if n % 2 else "down")
    stale = hub.poll(cid, instance=hub.instance, since=0, wait=5.0)
    assert stale["reset"] is True
    assert _types(stale) == ["snapshot"]


def test_poll_of_an_unknown_client_is_gone() -> None:
    hub = EventHub()
    with pytest.raises(UnknownClient):
        hub.poll("ghost", instance=hub.instance, since=0, wait=0)


def test_every_poll_lists_the_leases_still_held() -> None:
    now = [0.0]
    hub = EventHub(clock=lambda: now[0])
    cid, cursor = _register(hub, "clipboard", "cues")
    assert _poll(hub, cid, cursor)["leases"] == ["clipboard", "cues"]
    now[0] += 41  # silent for longer than the default 40 s lease
    assert hub.holder("cues") is None
    assert _poll(hub, cid, cursor)["leases"] == []  # the client must register again (takeover)


def test_long_poll_wakes_on_a_new_event() -> None:
    hub = EventHub()
    cid, cursor = _register(hub)
    threading.Timer(0.15, lambda: hub.publish_health("down")).start()
    started = time.monotonic()
    response = _poll(hub, cid, cursor, wait=10.0)
    assert time.monotonic() - started < 2.0
    assert _types(response) == ["health"]
    assert response["cursor"] == cursor + 1


def test_shutdown_event_wakes_pending_polls_and_is_published_once() -> None:
    hub = EventHub()
    cid, cursor = _register(hub)
    received: list[EventsResponse] = []
    thread = threading.Thread(target=lambda: received.append(_poll(hub, cid, cursor, wait=20.0)))
    thread.start()
    time.sleep(0.1)
    assert hub.publish_shutdown("restart") is True
    assert hub.publish_shutdown("user_quit") is False
    thread.join(timeout=5.0)
    assert _types(received[0]) == ["shutdown"]
    assert _data(received[0]["events"][0]) == {"reason": "restart"}


def test_health_event_only_on_change() -> None:
    hub = EventHub()
    cid, cursor = _register(hub)
    hub.publish_health("unknown")  # the initial state: no event
    hub.publish_health("ok")
    hub.publish_health("ok")
    hub.publish_health("down")
    assert [_data(e)["audio"] for e in _poll(hub, cid, cursor)["events"]] == ["ok", "down"]
    assert hub.audio == "down"


def test_ack_checks_client_and_instance() -> None:
    hub = EventHub()
    cid, _ = _register(hub, "clipboard")
    hub.publish_result("a", op_seq=1, kind="transcript", flow=None, final=True)
    hub.publish_result("b", op_seq=2, kind="transcript", flow=None, final=True)
    with pytest.raises(UnknownClient):
        hub.ack("ghost", instance=hub.instance, acks=[(1, "delivered")])
    with pytest.raises(InstanceMismatch):
        hub.ack(cid, instance="older", acks=[(1, "delivered")])
    assert hub.ack(cid, instance=hub.instance, acks=[(1, "delivered")]) == {"ok": True, "unacked": [2]}


def test_errors_and_warnings_carry_code_detail_message_and_op() -> None:
    hub = EventHub()
    cid, cursor = _register(hub)
    hub.publish_error("mic_unavailable", detail="PortAudio -9996", message="Microfone indisponível", op_seq=7)
    hub.publish_warning("no_speech", detail="1.0s of audio", message="Nenhuma fala", op_seq=7)
    error, warning = _poll(hub, cid, cursor)["events"]
    assert (error["type"], _data(error)["code"], _data(error)["detail"]) == (
        "error",
        "mic_unavailable",
        "PortAudio -9996",
    )
    assert (warning["type"], _data(warning)["code"], _data(warning)["op_seq"]) == ("warning", "no_speech", 7)


def test_nothing_is_appended_after_the_shutdown_event() -> None:
    """Clients consider the instance gone after `shutdown`: later work (a handler still
    finishing) is not journaled, and the daemon writes and beeps for itself again."""
    hub = EventHub()
    cid, cursor = _register(hub, "clipboard", "cues")
    hub.publish_shutdown("user_quit")
    after = hub.cursor
    hub.set_state(1, "recording", flow="clipboard", flow_kind="clipboard", client_id=None)
    hub.publish_health("down")
    assert hub.publish_result("late", op_seq=1, kind="transcript", flow=None, final=True) == Publication(
        True, True, True
    )
    assert hub.publish_error("transcription_failed", detail="", message="", op_seq=1).play_cue is True
    assert hub.cursor == after
    assert hub.state()["state"] == "recording"  # the state itself still follows the engine
    assert hub.audio == "down"
    assert [e["type"] for e in _poll(hub, cid, cursor)["events"]] == ["shutdown"]


def test_polling_reports_a_long_poll_in_flight() -> None:
    hub = EventHub()
    cid, cursor = _register(hub)
    assert hub.polling(cid) is False
    thread = threading.Thread(target=lambda: _poll(hub, cid, cursor, wait=5.0))
    thread.start()
    deadline = time.monotonic() + 5.0
    while not hub.polling(cid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert hub.polling(cid) is True
    hub.publish_health("down")
    thread.join(timeout=5.0)
    assert hub.polling(cid) is False
