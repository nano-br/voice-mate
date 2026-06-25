from __future__ import annotations

from app.core.session_status import SessionStatus


def test_starts_idle_and_empty() -> None:
    s = SessionStatus()
    assert s.get() == (0, "")
    assert s.status(None, "all")["state"] == "idle"
    assert s.result(None, "all")["text"] == ""


def test_register_returns_unique_ids() -> None:
    s = SessionStatus()
    a, b = s.register(), s.register()
    assert a != b
    assert s.is_registered(a)
    assert s.is_registered(b)
    assert not s.is_registered("nao-existe")


def test_operation_lifecycle_and_result_correlation() -> None:
    s = SessionStatus()
    cid = s.register()
    s.set_operation(1, "recording", "clipboard", cid)
    st = s.status(cid, "all")
    assert st["state"] == "recording"
    assert st["op_seq"] == 1
    assert st["flow"] == "clipboard"
    assert st["is_yours"] is True

    s.set_operation(1, "processing", "clipboard", cid)
    s.record_result("olá mundo")
    s.mark_idle(1)

    assert s.status(cid, "all")["state"] == "idle"
    res = s.result(cid, "all")
    assert res["seq"] == 1
    assert res["text"] == "olá mundo"
    assert res["op_seq"] == 1  # result correlated with the operation that produced it
    assert res["client_id"] == cid


def test_result_seq_is_monotonic() -> None:
    """The consumer compares seq to know whether the status is newer than the one it stored."""
    s = SessionStatus()
    s.record_result("a")
    s.record_result("b")
    assert s.result(None, "all")["seq"] == 2
    assert s.get() == (2, "b")


def test_result_since_drains_next_unseen_in_order() -> None:
    """With `since`, returns the NEXT unseen one — the consumer drains without losing any."""
    s = SessionStatus()
    s.record_result("a")  # seq 1
    s.record_result("b")  # seq 2
    s.record_result("c")  # seq 3

    # Consumer started at 0: drains 1 → 2 → 3, in order.
    assert s.result(None, "all", since=0)["text"] == "a"
    assert s.result(None, "all", since=1)["text"] == "b"
    assert s.result(None, "all", since=2)["text"] == "c"


def test_result_since_returns_latest_when_drained() -> None:
    """With nothing new (since >= last seq), returns the last one (seq <= since) and the
    consumer stops draining."""
    s = SessionStatus()
    s.record_result("a")  # seq 1
    s.record_result("b")  # seq 2

    res = s.result(None, "all", since=2)
    assert res["seq"] == 2  # does not advance: nothing > 2
    assert res["text"] == "b"


def test_result_without_since_is_backward_compatible() -> None:
    """Without `since`, the old behavior (last result) is preserved."""
    s = SessionStatus()
    s.record_result("a")
    s.record_result("b")
    assert s.result(None, "all")["text"] == "b"


def test_result_since_drains_per_client_in_mine_scope() -> None:
    """The buffer is also per client: scope='mine' drains only what THAT client started."""
    s = SessionStatus()
    alice, bob = s.register(), s.register()

    s.set_operation(1, "processing", "clipboard", alice)
    s.record_result("alice-1")  # global seq 1
    s.set_operation(2, "processing", "clipboard", bob)
    s.record_result("bob-1")  # global seq 2
    s.set_operation(3, "processing", "clipboard", alice)
    s.record_result("alice-2")  # global seq 3

    # Alice drains only hers, in order, ignoring bob's result in between.
    assert s.result(alice, "mine", since=0)["text"] == "alice-1"
    assert s.result(alice, "mine", since=1)["text"] == "alice-2"  # skips seq 2 (bob)


def test_scope_mine_isolates_per_client() -> None:
    s = SessionStatus()
    alice, bob = s.register(), s.register()

    s.set_operation(1, "processing", "clipboard", alice)
    s.record_result("da alice")
    s.set_operation(2, "processing", "clipboard", bob)
    s.record_result("do bob")

    # scope=all → the global one (latest across all)
    assert s.result(alice, "all")["text"] == "do bob"
    assert s.status(alice, "all")["is_yours"] is False  # the current op is bob's

    # scope=mine → only what EACH one started
    assert s.result(alice, "mine")["text"] == "da alice"
    assert s.result(bob, "mine")["text"] == "do bob"
    assert s.status(alice, "mine")["op_seq"] == 1
    assert s.status(bob, "mine")["op_seq"] == 2


def test_scope_mine_unknown_client_is_idle_empty() -> None:
    s = SessionStatus()
    s.set_operation(1, "recording", "clipboard", "alguem")
    assert s.status("fantasma", "mine")["state"] == "idle"
    assert s.result("fantasma", "mine")["text"] == ""


def test_mark_idle_ignores_superseded_operation() -> None:
    """If a new operation opened on top, the mark_idle of the previous one is ignored."""
    s = SessionStatus()
    s.set_operation(1, "processing", "clipboard", None)
    s.set_operation(2, "recording", "clipboard", None)  # new recording on top
    s.mark_idle(1)  # late finalization of op 1
    assert s.status(None, "all")["state"] == "recording"
    assert s.status(None, "all")["op_seq"] == 2
