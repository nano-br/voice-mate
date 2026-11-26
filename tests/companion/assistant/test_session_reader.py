"""Session reader: transcript parsing, following a growing file, history and live sessions."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.companion.assistant import session_reader as sr


@pytest.fixture(autouse=True)
def claude_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude-home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def _line(**kw: object) -> str:
    return json.dumps(kw)


def _user(text: object, uuid: str = "u1", **kw: object) -> str:
    return _line(type="user", uuid=uuid, timestamp="t-" + uuid, message={"role": "user", "content": text}, **kw)


def _assistant(text: str, mid: str = "m1", stop: str | None = None, uuid: str = "a1", **kw: object) -> str:
    return _line(
        type="assistant",
        uuid=uuid,
        timestamp="t-" + uuid,
        message={"id": mid, "role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop},
        **kw,
    )


def _write(path: Path, *lines: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return path


# --- locations ---------------------------------------------------------------


def test_claude_home_honours_env_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sr.claude_home() == Path(os.environ["CLAUDE_CONFIG_DIR"])
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert sr.claude_home() == Path.home() / ".claude"


def test_project_slug_replaces_non_alphanumerics() -> None:
    assert sr.project_slug("C:\\Users\\a\\.b c") == "C--Users-a--b-c"
    assert sr.project_slug("/home/alli/x_y") == "-home-alli-x-y"


def test_project_slug_long_path_gets_hash_suffix() -> None:
    cwd = "/" + "a" * 250
    slug = sr.project_slug(cwd)
    assert slug[:200] == "-" + "a" * 199
    assert slug[200] == "-"
    assert slug[201:] == sr._simple_hash(cwd)
    assert sr.project_slug("/" + "a" * 199) == "-" + "a" * 199  # exactly 200: untouched


def test_simple_hash_matches_js_reference() -> None:
    # JS: h = ((h << 5) - h + charCode) | 0, then Math.abs(h).toString(36)
    assert sr._simple_hash("") == "0"
    assert sr._simple_hash("a") == "2p"  # 97
    assert sr._simple_hash("abc") == "22ci"  # 96354
    assert int(sr._simple_hash("x" * 100), 36) < 2**31 + 1


def test_project_dir_is_case_insensitive(claude_dir: Path) -> None:
    folder = claude_dir / "projects" / "c--Users-a-proj"
    folder.mkdir(parents=True)
    assert sr.project_dir("C:\\Users\\a\\proj") == folder
    assert sr.project_dir("C:\\Users\\a\\other") is None


def test_project_dir_without_projects_root() -> None:
    assert sr.project_dir("C:\\x") is None


def test_find_session_file(claude_dir: Path) -> None:
    target = _write(claude_dir / "projects" / "p1" / "abc.jsonl", _user("hi"))
    _write(claude_dir / "projects" / "p2" / "def.jsonl", _user("yo"))
    assert sr.find_session_file("abc") == target
    assert sr.find_session_file("missing") is None
    assert sr.find_session_file("../abc") is None


# --- parse_line --------------------------------------------------------------


def test_parse_user_and_assistant_text() -> None:
    [u] = sr.parse_line(_user("hello", uuid="u9"))
    assert (u.kind, u.text, u.timestamp, u.uuid) == ("user", "hello", "t-u9", "u9")
    [a] = sr.parse_line(_assistant("answer", mid="m7"))
    assert (a.kind, a.text, a.message_id) == ("assistant_text", "answer", "m7")


def test_user_text_blocks_are_joined() -> None:
    blocks = [{"type": "text", "text": "a"}, {"type": "image"}, {"type": "text", "text": "b"}]
    [u] = sr.parse_line(_user(blocks))
    assert u.text == "ab"


def test_parse_skips_meta_sidechain_tool_results_and_unknown() -> None:
    tool_result = [{"type": "tool_result", "tool_use_id": "x", "content": "out"}]
    assert sr.parse_line(_user("x", isMeta=True)) == []
    assert sr.parse_line(_user("x", isSidechain=True)) == []
    assert sr.parse_line(_user("summary", isCompactSummary=True)) == []
    assert sr.parse_line(_user(tool_result)) == []
    assert sr.parse_line(_assistant("x", isSidechain=True)) == []
    assert sr.parse_line(_line(type="file-history-snapshot")) == []
    assert sr.parse_line(_line(type="queue-operation", operation="enqueue")) == []
    assert sr.parse_line("not json") == []
    assert sr.parse_line("[1, 2]") == []
    assert sr.parse_line(b"\xff\xfe") == []


def test_assistant_tool_use_only_line_yields_nothing() -> None:
    raw = _line(type="assistant", message={"id": "m", "content": [{"type": "tool_use", "name": "Bash"}]})
    assert sr.parse_line(raw) == []


def test_assistant_blocks_joined_in_one_line() -> None:
    content = [{"type": "text", "text": "one "}, {"type": "tool_use", "name": "x"}, {"type": "text", "text": "two"}]
    [a] = sr.parse_line(_line(type="assistant", message={"id": "m", "content": content}))
    assert a.text == "one two"


def test_turn_end_by_stop_reason_and_by_hook_summary() -> None:
    assert [e.kind for e in sr.parse_line(_assistant("done", stop="end_turn"))] == ["assistant_text", "turn_end"]
    assert [e.kind for e in sr.parse_line(_assistant("more", stop="tool_use"))] == ["assistant_text"]
    [t] = sr.parse_line(_line(type="system", subtype="stop_hook_summary", timestamp="ts"))
    assert (t.kind, t.timestamp) == ("turn_end", "ts")


def test_system_compact_and_error() -> None:
    [c] = sr.parse_line(_line(type="system", subtype="compact_boundary"))
    assert c.kind == "compact"
    [e] = sr.parse_line(_line(type="system", subtype="api_error", error={"formatted": "overloaded"}))
    assert (e.kind, e.text) == ("error", "overloaded")
    assert sr.parse_line(_line(type="system", subtype="informational")) == []


def test_titles() -> None:
    [t] = sr.parse_line(_line(type="custom-title", customTitle="Mine"))
    assert (t.kind, t.text) == ("title", "Mine")
    [t] = sr.parse_line(_line(type="ai-title", aiTitle="Auto"))
    assert t.text == "Auto"


# --- Tail --------------------------------------------------------------------


def test_tail_from_start_emits_each_event_once(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", _user("q"), _assistant("a", stop="end_turn"))
    tail = sr.Tail(path, from_start=True)
    assert [e.kind for e in tail.poll()] == ["user", "assistant_text", "turn_end"]
    assert tail.poll() == []


def test_tail_default_skips_existing_content(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", _user("old"))
    tail = sr.Tail(path)
    assert tail.poll() == []
    with open(path, "ab") as f:
        f.write((_user("new", uuid="u2") + "\n").encode())
    assert [e.text for e in tail.poll()] == ["new"]


def test_tail_holds_back_a_line_appended_in_two_halves(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_bytes(b"")
    tail = sr.Tail(path, from_start=True)
    raw = (_user("q") + "\n" + _assistant("h\u00e9llo w\u00f6rld", stop="end_turn") + "\n").encode()
    cut = raw.index(b"stop_reason") - 5  # inside the second line
    with open(path, "ab") as f:
        f.write(raw[:cut])
    assert [e.kind for e in tail.poll()] == ["user"]
    with open(path, "ab") as f:
        f.write(raw[cut:])
    events = tail.poll()
    assert [e.kind for e in events] == ["assistant_text", "turn_end"]
    assert events[0].text == "h\u00e9llo w\u00f6rld"
    assert tail.poll() == []


def test_tail_missing_file_then_created(tmp_path: Path) -> None:
    path = tmp_path / "later.jsonl"
    tail = sr.Tail(path, from_start=True)
    assert tail.poll() == []
    _write(path, _user("hi"))
    assert [e.kind for e in tail.poll()] == ["user"]


def test_tail_restarts_when_file_shrinks(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", _user("first message that is long"), _user("second", uuid="u2"))
    tail = sr.Tail(path, from_start=True)
    assert len(tail.poll()) == 2
    _write(path, _user("x"))
    assert [e.text for e in tail.poll()] == ["x"]


def test_tail_skips_bad_lines_and_dedupes_titles_and_turn_ends(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        "garbage",
        _line(type="ai-title", aiTitle="T"),
        _user("q"),
        _assistant("a", stop="end_turn"),
        _line(type="system", subtype="stop_hook_summary"),
        _line(type="ai-title", aiTitle="T"),
        _line(type="ai-title", aiTitle="T2"),
    )
    events = sr.Tail(path, from_start=True).poll()
    assert [(e.kind, e.text) for e in events] == [
        ("title", "T"),
        ("user", "q"),
        ("assistant_text", "a"),
        ("turn_end", ""),
        ("title", "T2"),
    ]


def test_tail_turn_end_without_open_turn_is_dropped(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", _line(type="system", subtype="stop_hook_summary"))
    assert sr.Tail(path, from_start=True).poll() == []


# --- last_messages -----------------------------------------------------------


def test_last_messages_joins_reply_lines_and_limits(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        _user("q1", uuid="u1"),
        _assistant("part one. ", mid="m1", uuid="a1"),
        _line(type="assistant", message={"id": "m1", "content": [{"type": "tool_use", "name": "x"}]}),
        _assistant("part two.", mid="m1", uuid="a2", stop="end_turn"),
        _user("tool", uuid="u2", isMeta=True),
        _user("q2", uuid="u3"),
        _assistant("other", mid="m2", uuid="a3"),
    )
    msgs = sr.last_messages(path, 10)
    assert [(m.role, m.text) for m in msgs] == [
        ("user", "q1"),
        ("assistant", "part one. part two."),
        ("user", "q2"),
        ("assistant", "other"),
    ]
    assert msgs[1].uuid == "a1"
    assert [m.text for m in sr.last_messages(path, 2)] == ["q2", "other"]
    assert sr.last_messages(path, 0) == []


def test_last_messages_keeps_what_was_said_before_a_compaction(tmp_path: Path) -> None:
    """A conversation that was just summarized is still the same conversation: a reader that saw
    only what follows took one hard at work for one that said a single line and stopped."""
    path = _write(
        tmp_path / "s.jsonl",
        _user("before"),
        _assistant("old answer", mid="m1"),
        _line(type="system", subtype="compact_boundary"),
        _user("summary of the past", isCompactSummary=True),
        _user("after", uuid="u2"),
        _assistant("new answer", mid="m2", uuid="a2"),
    )
    assert [m.text for m in sr.last_messages(path, 10)] == ["before", "old answer", "after", "new answer"]


def test_last_messages_missing_file(tmp_path: Path) -> None:
    assert sr.last_messages(tmp_path / "nope.jsonl", 5) == []


# --- live sessions -----------------------------------------------------------


def test_live_sessions_reads_registry_and_ignores_bad_files(claude_dir: Path) -> None:
    sessions = claude_dir / "sessions"
    sessions.mkdir()
    (sessions / "100.json").write_text(
        json.dumps({"sessionId": "s-busy", "pid": 100, "status": "busy", "name": "Work"}), encoding="utf-8"
    )
    (sessions / "200.json").write_text(json.dumps({"sessionId": "s-idle", "status": "idle"}), encoding="utf-8")
    (sessions / "300.json").write_text(json.dumps({"sessionId": "s-odd", "status": "weird"}), encoding="utf-8")
    (sessions / "400.json").write_text("{broken", encoding="utf-8")
    (sessions / "500.json").write_text(json.dumps({"status": "idle"}), encoding="utf-8")
    (sessions / "600.json").write_text("[]", encoding="utf-8")
    (sessions / "100.key").write_text(json.dumps({"sessionId": "secret", "status": "busy"}), encoding="utf-8")
    live = sr.live_sessions()
    assert set(live) == {"s-busy", "s-idle", "s-odd"}
    assert live["s-busy"] == sr.LiveSession("s-busy", 100, "busy", "Work")
    assert live["s-idle"].pid == 200
    assert live["s-idle"].status == "idle"
    assert live["s-odd"].status == "unknown"


def test_live_sessions_without_folder() -> None:
    assert sr.live_sessions() == {}


def test_live_sessions_never_opens_key_files(claude_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = claude_dir / "sessions"
    sessions.mkdir()
    (sessions / "1.key").write_text("secret", encoding="utf-8")
    (sessions / "2.json").write_text(json.dumps({"sessionId": "s", "status": "idle"}), encoding="utf-8")
    opened: list[str] = []
    real_open = open

    def spy(file: object, *a: object, **k: object) -> object:
        opened.append(str(file))
        return real_open(file, *a, **k)  # type: ignore[call-overload]

    monkeypatch.setattr("builtins.open", spy)
    assert set(sr.live_sessions()) == {"s"}
    assert not any(p.endswith(".key") for p in opened)


# --- list_conversations ------------------------------------------------------


def test_list_conversations_newest_first_with_titles(claude_dir: Path) -> None:
    old = _write(claude_dir / "projects" / "p1" / "old.jsonl", _user("a"), _line(type="ai-title", aiTitle="Old AI"))
    new = _write(
        claude_dir / "projects" / "p2" / "new.jsonl",
        _user("b"),
        _line(type="ai-title", aiTitle="Auto"),
        _line(type="custom-title", customTitle="Chosen"),
        _line(type="ai-title", aiTitle="Auto later"),
    )
    plain = _write(claude_dir / "projects" / "p2" / "plain.jsonl", _user("c"))
    os.utime(old, (1000, 1000))
    os.utime(new, (3000, 3000))
    os.utime(plain, (2000, 2000))
    items = sr.list_conversations(limit=10)
    assert [i.session_id for i in items] == ["new", "plain", "old"]
    assert [i.title for i in items] == ["Chosen", "", "Old AI"]
    assert items[0].project == "p2"
    assert items[0].path == new
    assert items[0].modified == 3000
    assert [i.session_id for i in sr.list_conversations(limit=1)] == ["new"]


def test_list_conversations_without_projects() -> None:
    assert sr.list_conversations() == []


def test_find_sessions_by_title_matches_the_custom_title_exactly(claude_dir: Path) -> None:
    title = "Central é"
    older = _write(
        claude_dir / "projects" / "p1" / "older.jsonl", _user("a"), _line(type="custom-title", customTitle=title)
    )
    newer = _write(
        claude_dir / "projects" / "p2" / "newer.jsonl",
        _user("b"),
        _line(type="custom-title", customTitle="Old name"),
        _line(type="custom-title", customTitle=title),
    )
    renamed = _write(
        claude_dir / "projects" / "p2" / "renamed.jsonl",
        _line(type="custom-title", customTitle=title),
        _line(type="custom-title", customTitle="Something else"),
    )
    _write(claude_dir / "projects" / "p2" / "ai.jsonl", _line(type="ai-title", aiTitle=title))
    _write(claude_dir / "projects" / "p2" / "prefix.jsonl", _line(type="custom-title", customTitle=title + " 2"))
    os.utime(older, (1000, 1000))
    os.utime(newer, (3000, 3000))
    os.utime(renamed, (2000, 2000))
    found = sr.find_sessions_by_title(title)
    assert [i.session_id for i in found] == ["newer", "older"]
    assert found[0].title == title
    assert found[0].path == newer


def test_find_sessions_by_title_blank_or_unknown(claude_dir: Path) -> None:
    _write(claude_dir / "projects" / "p1" / "a.jsonl", _line(type="custom-title", customTitle="X"))
    assert sr.find_sessions_by_title("") == []
    assert sr.find_sessions_by_title("   ") == []
    assert sr.find_sessions_by_title("Y") == []


# --- a prompt taken in by a running turn -------------------------------------


def _queued(prompt: object, uuid: str = "q1", **attachment: object) -> str:
    body = {"type": "queued_command", "prompt": prompt, "commandMode": "prompt", **attachment}
    return _line(type="attachment", uuid=uuid, timestamp="t-" + uuid, attachment=body)


def test_a_prompt_taken_in_by_a_running_turn_is_a_user_event() -> None:
    [event] = sr.parse_line(_queued("how is it going?", origin={"kind": "human"}, humanTurn=True))
    assert (event.kind, event.text, event.queued) == ("user", "how is it going?", True)
    [blocks] = sr.parse_line(_queued([{"type": "text", "text": "in blocks"}]))
    assert (blocks.text, blocks.queued) == ("in blocks", True)
    [plain] = sr.parse_line(_user("a turn of its own"))
    assert plain.queued is False


def test_a_task_notification_and_other_attachments_are_not_prompts() -> None:
    assert sr.parse_line(_queued("<task-notification>", origin={"kind": "task-notification"})) == []
    assert sr.parse_line(_queued("x", commandMode="task-notification")) == []
    assert sr.parse_line(_queued("")) == []
    assert sr.parse_line(_line(type="attachment", attachment={"type": "environment"})) == []
    assert sr.parse_line(_line(type="attachment", attachment="odd")) == []
    assert sr.parse_line(_queued("side", isSidechain=True)[:-1] + ', "isSidechain": true}') == []


def test_last_messages_show_a_prompt_taken_in_by_a_running_turn(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        _user("start"),
        _assistant("on it", mid="m1"),
        _queued("and the other thing?"),
        _assistant("both done", mid="m2", uuid="a2", stop="end_turn"),
    )
    assert [(m.role, m.text) for m in sr.last_messages(path, 10)] == [
        ("user", "start"),
        ("assistant", "on it"),
        ("user", "and the other thing?"),
        ("assistant", "both done"),
    ]


# --- where a conversation is --------------------------------------------------


def _tool_use(uuid: str = "t1") -> str:
    message = {
        "id": "mt",
        "role": "assistant",
        "content": [{"type": "tool_use", "name": "Bash"}],
        "stop_reason": "tool_use",
    }
    return _line(type="assistant", uuid=uuid, message=message)


def test_activity_tells_a_running_turn_from_a_finished_one(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", _user("go"), _assistant("working on it", mid="m1"), _tool_use())
    os.utime(path, (500.0, 500.0))
    assert sr.activity(path) == sr.Activity(True, 500.0)
    with open(path, "a", encoding="utf-8") as f:
        f.write(_assistant("done", mid="m2", uuid="a2", stop="end_turn") + "\n")
        f.write(_line(type="ai-title", aiTitle="A title") + "\n")  # written after the end: not a turn
    assert sr.activity(path).turn_open is False
    with open(path, "a", encoding="utf-8") as f:
        f.write(_line(type="system", subtype="stop_hook_summary") + "\n")
        f.write(_queued("one more thing") + "\n")
    assert sr.activity(path).turn_open is True


def test_activity_of_a_tool_result_a_summary_and_a_missing_file(tmp_path: Path) -> None:
    result = [{"type": "tool_result", "content": "ok"}]
    path = _write(tmp_path / "s.jsonl", _assistant("done", stop="end_turn"), _user(result, uuid="u2"))
    assert sr.activity(path).turn_open is True  # the tool answered: the turn goes on
    path = _write(tmp_path / "t.jsonl", _assistant("done", stop="end_turn"), _user("summary", isCompactSummary=True))
    assert sr.activity(path).turn_open is False
    assert sr.activity(tmp_path / "nope.jsonl") == sr.Activity(False, 0.0)
    assert sr.activity(_write(tmp_path / "e.jsonl", "not json")).turn_open is False


def test_activity_reads_only_the_end_of_a_big_transcript(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sr, "ACTIVITY_TAIL_BYTES", 400)
    path = _write(tmp_path / "s.jsonl", _user("go"), _tool_use(), _assistant("x" * 2000, stop="end_turn"))
    assert sr.activity(path).turn_open is False  # the last line alone is longer than the window
    path = _write(tmp_path / "t.jsonl", _assistant("done", stop="end_turn"), _user("y" * 2000, uuid="u2"))
    assert sr.activity(path).turn_open is True


def test_activity_of_a_turn_that_ended_without_an_answer(tmp_path: Path) -> None:
    stopped = _user("[Request interrupted by user for tool use]", uuid="u2")
    assert sr.activity(_write(tmp_path / "a.jsonl", _user("go"), _tool_use(), stopped)).turn_open is False
    cut = _assistant("too long", stop="max_tokens")
    assert sr.activity(_write(tmp_path / "b.jsonl", _user("go"), cut)).turn_open is False
    failed = _line(type="assistant", isApiErrorMessage=True, message={"role": "assistant", "content": []})
    assert sr.activity(_write(tmp_path / "c.jsonl", _user("go"), failed)).turn_open is False
    command = _user("<command-name>/model</command-name>", uuid="u3")
    path = _write(tmp_path / "d.jsonl", _assistant("done", stop="end_turn"), command)
    assert sr.activity(path).turn_open is False  # a local command is not a turn


def test_activity_when_the_closing_line_of_a_message_is_not_its_last(tmp_path: Path) -> None:
    closing = _assistant("", mid="m1", stop="end_turn")
    path = _write(tmp_path / "s.jsonl", _user("go"), closing, _assistant("the answer", mid="m1", uuid="a2"))
    assert sr.activity(path).turn_open is False


def test_a_turn_that_dies_on_an_error_ends(tmp_path: Path) -> None:
    line = _assistant("The context is full.", mid="e1", stop="stop_sequence", isApiErrorMessage=True)
    kinds = [(e.kind, e.text) for e in sr.parse_line(line)]
    assert kinds == [("assistant_text", "The context is full."), ("error", "The context is full."), ("turn_end", "")]
    assert [e.kind for e in sr.parse_line(_assistant("cut", stop="max_tokens"))] == ["assistant_text"]
    assert [e.kind for e in sr.parse_line(_tool_use())] == []


# --- activity by the last message, not by the file's modification time -------------------------


def _at(iso: str, kind: str = "user", **kw: object) -> str:
    return _line(type=kind, uuid="x", timestamp=iso, message={"role": kind, "content": "hi"}, **kw)


def test_last_message_time_skips_the_bookkeeping_the_desktop_appends(claude_dir: Path) -> None:
    path = _write(
        claude_dir / "projects" / "p" / "s.jsonl",
        _at("2026-10-08T19:59:40.000Z"),
        _at("2026-10-08T19:59:43.500Z", "assistant"),
        _at("2026-11-26T18:00:00.000Z", isMeta=True),  # a meta line written when the session is loaded
        _line(type="last-prompt", lastPrompt="hello"),
        _line(type="cost-state", totalCostUSD=3.3),
        _line(type="mode", mode="normal"),
    )
    assert sr.last_message_time(path) == datetime(2026, 10, 8, 19, 59, 43, 500000, tzinfo=UTC).timestamp()


def test_last_message_time_without_messages_or_file(claude_dir: Path) -> None:
    path = _write(claude_dir / "projects" / "p" / "s.jsonl", _line(type="mode", mode="normal"))
    assert sr.last_message_time(path) is None
    assert sr.last_message_time(claude_dir / "missing.jsonl") is None
    assert sr.last_message_time(_write(claude_dir / "projects" / "p" / "t.jsonl", _user("a"))) is None  # no ISO time


def test_last_message_time_widens_the_window_past_long_bookkeeping(
    claude_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sr, "LAST_MESSAGE_TAIL_BYTES", 100)
    filler = [_line(type="cost-state", pad="z" * 80) for _ in range(20)]
    path = _write(claude_dir / "projects" / "p" / "s.jsonl", _at("2026-10-08T10:00:00Z"), *filler)
    assert sr.last_message_time(path) == datetime(2026, 10, 8, 10, tzinfo=UTC).timestamp()
    monkeypatch.setattr(sr, "LAST_MESSAGE_MAX_BYTES", 400)
    assert sr.last_message_time(path) is None


def test_list_conversations_orders_by_the_last_message(claude_dir: Path) -> None:
    def conv(name: str, iso: str, mtime: float) -> Path:
        path = _write(
            claude_dir / "projects" / "p" / f"{name}.jsonl", _at(iso), _line(type="custom-title", customTitle=name)
        )
        os.utime(path, (mtime, mtime))
        return path

    base = datetime(2026, 11, 1, tzinfo=UTC).timestamp()
    conv("touched-old", "2026-10-08T10:00:00Z", base + 9000)  # loaded by the Desktop today, last used in October
    conv("recent", "2026-11-01T02:00:00Z", base + 7300)
    conv("middle", "2026-10-20T10:00:00Z", base + 100)
    items = sr.list_conversations(limit=2)
    assert [i.title for i in items] == ["recent", "middle"]
    assert items[0].modified == datetime(2026, 11, 1, 2, tzinfo=UTC).timestamp()
    assert [i.title for i in sr.list_conversations(limit=5)] == ["recent", "middle", "touched-old"]


def test_list_conversations_never_dates_a_file_after_its_modification(claude_dir: Path) -> None:
    path = _write(claude_dir / "projects" / "p" / "s.jsonl", _at("2030-01-01T00:00:00Z"))
    os.utime(path, (5000, 5000))
    assert sr.list_conversations(limit=1)[0].modified == 5000


def test_fold_text_and_search_by_part_of_the_title(claude_dir: Path) -> None:
    assert sr.fold_text("  Tradução  GTAVXR ") == "traducao gtavxr"
    for name, title, iso in (
        ("a", "Tradução GTAVXR mod pt-BR", "2026-10-01T00:00:00Z"),
        ("b", "GTAVXR compatibilidade de mods", "2026-10-05T00:00:00Z"),
        ("c", "Site pessoal e domínio", "2026-10-09T00:00:00Z"),
    ):
        _write(claude_dir / "projects" / "p" / f"{name}.jsonl", _at(iso), _line(type="custom-title", customTitle=title))
    assert [i.session_id for i in sr.search_conversations("gtavxr")] == ["b", "a"]
    assert [i.session_id for i in sr.search_conversations("traducao MOD")] == ["a"]
    assert [i.session_id for i in sr.search_conversations("DOMINIO site")] == ["c"]
    assert sr.search_conversations("gtavxr", limit=1)[0].session_id == "b"
    assert sr.search_conversations("   ") == []
    assert sr.search_conversations("nothing like it") == []


def test_runs_of_scheduled_tasks_are_recognized_and_left_out(claude_dir: Path) -> None:
    run_prompt = '<scheduled-task name="check" file="C:\\t\\SKILL.md">\nThis is an automated run.\n</scheduled-task>'
    run = _write(
        claude_dir / "projects" / "p" / "run.jsonl",
        _line(type="queue-operation", operation="enqueue"),
        _at("2026-11-25T05:00:00Z", isMeta=True),
        _line(type="user", timestamp="2026-11-25T05:00:01Z", message={"role": "user", "content": run_prompt}),
        _line(type="custom-title", customTitle="Check the board"),
    )
    blocks = _write(
        claude_dir / "projects" / "p" / "blocks.jsonl",
        _line(
            type="user",
            timestamp="2026-11-25T04:00:00Z",
            message={"role": "user", "content": [{"type": "text", "text": "  " + run_prompt}]},
        ),
        _line(type="custom-title", customTitle="Check again"),
    )
    talk = _write(
        claude_dir / "projects" / "p" / "talk.jsonl",
        _at("2026-11-20T10:00:00Z"),
        _line(type="custom-title", customTitle="Check my notes"),
    )
    assert sr.is_scheduled_run(run) and sr.is_scheduled_run(blocks)
    assert not sr.is_scheduled_run(talk)
    assert not sr.is_scheduled_run(claude_dir / "missing.jsonl")
    # A first prompt longer than the window is cut, and still recognized from its start.
    long_run = _write(
        claude_dir / "projects" / "q" / "long.jsonl",
        _line(type="user", message={"role": "user", "content": run_prompt + "x" * sr.SCHEDULED_HEAD_BYTES}),
    )
    long_talk = _write(
        claude_dir / "projects" / "q" / "long_talk.jsonl",
        _line(type="user", message={"role": "user", "content": "hello " + "x" * sr.SCHEDULED_HEAD_BYTES}),
    )
    assert sr.is_scheduled_run(long_run) and not sr.is_scheduled_run(long_talk)
    long_run.unlink()
    long_talk.unlink()
    assert [i.title for i in sr.list_conversations(5)] == ["Check the board", "Check again", "Check my notes"]
    assert [i.title for i in sr.list_conversations(5, skip_scheduled=True)] == ["Check my notes"]
    assert [i.title for i in sr.search_conversations("check")] == ["Check my notes"]
