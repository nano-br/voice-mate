"""Session reader: transcript parsing, following a growing file, history and live sessions."""

from __future__ import annotations

import json
import os
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


def test_last_messages_starts_after_last_compact_boundary(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        _user("before"),
        _assistant("old answer", mid="m1"),
        _line(type="system", subtype="compact_boundary"),
        _user("summary of the past", isCompactSummary=True),
        _user("after", uuid="u2"),
        _assistant("new answer", mid="m2", uuid="a2"),
    )
    assert [m.text for m in sr.last_messages(path, 10)] == ["after", "new answer"]


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
