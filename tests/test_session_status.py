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
    assert res["op_seq"] == 1  # resultado correlacionado à operação que o gerou
    assert res["client_id"] == cid


def test_result_seq_is_monotonic() -> None:
    """O consumidor compara o seq para saber se o status é mais novo que o guardado."""
    s = SessionStatus()
    s.record_result("a")
    s.record_result("b")
    assert s.result(None, "all")["seq"] == 2
    assert s.get() == (2, "b")


def test_result_since_drains_next_unseen_in_order() -> None:
    """Com `since`, devolve o PRÓXIMO não-visto — o consumidor drena sem perder."""
    s = SessionStatus()
    s.record_result("a")  # seq 1
    s.record_result("b")  # seq 2
    s.record_result("c")  # seq 3

    # Consumidor começou em 0: drena 1 → 2 → 3, em ordem.
    assert s.result(None, "all", since=0)["text"] == "a"
    assert s.result(None, "all", since=1)["text"] == "b"
    assert s.result(None, "all", since=2)["text"] == "c"


def test_result_since_returns_latest_when_drained() -> None:
    """Sem nada novo (since >= último seq), devolve o último (seq <= since) e o
    consumidor para de drenar."""
    s = SessionStatus()
    s.record_result("a")  # seq 1
    s.record_result("b")  # seq 2

    res = s.result(None, "all", since=2)
    assert res["seq"] == 2  # não avança: nada > 2
    assert res["text"] == "b"


def test_result_without_since_is_backward_compatible() -> None:
    """Sem `since`, o comportamento antigo (último resultado) é preservado."""
    s = SessionStatus()
    s.record_result("a")
    s.record_result("b")
    assert s.result(None, "all")["text"] == "b"


def test_result_since_drains_per_client_in_mine_scope() -> None:
    """O buffer também é por cliente: scope='mine' drena só o que ELE iniciou."""
    s = SessionStatus()
    alice, bob = s.register(), s.register()

    s.set_operation(1, "processing", "clipboard", alice)
    s.record_result("alice-1")  # global seq 1
    s.set_operation(2, "processing", "clipboard", bob)
    s.record_result("bob-1")  # global seq 2
    s.set_operation(3, "processing", "clipboard", alice)
    s.record_result("alice-2")  # global seq 3

    # Alice drena só os dela, na ordem, ignorando o resultado do bob no meio.
    assert s.result(alice, "mine", since=0)["text"] == "alice-1"
    assert s.result(alice, "mine", since=1)["text"] == "alice-2"  # pula o seq 2 (bob)


def test_scope_mine_isolates_per_client() -> None:
    s = SessionStatus()
    alice, bob = s.register(), s.register()

    s.set_operation(1, "processing", "clipboard", alice)
    s.record_result("da alice")
    s.set_operation(2, "processing", "clipboard", bob)
    s.record_result("do bob")

    # scope=all → o global (último de todos)
    assert s.result(alice, "all")["text"] == "do bob"
    assert s.status(alice, "all")["is_yours"] is False  # a op corrente é do bob

    # scope=mine → só o que CADA um iniciou
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
    """Se uma nova operação abriu por cima, o mark_idle da anterior é ignorado."""
    s = SessionStatus()
    s.set_operation(1, "processing", "clipboard", None)
    s.set_operation(2, "recording", "clipboard", None)  # nova gravação por cima
    s.mark_idle(1)  # finalização tardia da op 1
    assert s.status(None, "all")["state"] == "recording"
    assert s.status(None, "all")["op_seq"] == 2
