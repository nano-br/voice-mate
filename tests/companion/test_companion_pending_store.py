"""The Not copied list on disk (app/companion/pending_store.py): always under tmp_path."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

from app.companion import pending_store as pending_store_module
from app.companion.contract import RecentItem
from app.companion.delivery import PENDING_LIMIT
from app.companion.pending_store import PENDING_FILE_VERSION, PendingStore, dump_pending, parse_pending
from app.protocol.models import ResultRecord


def item(seq: int, text: str = "", *, instance: str = "inst-a", delivery: str = "failed") -> RecentItem:
    return RecentItem(
        instance,
        ResultRecord(
            result_seq=seq,
            op_seq=seq + 100,
            kind="ai_response" if seq % 2 else "transcript",
            flow="claude_chat" if seq % 2 else "clipboard",
            text=text or f"text {seq}",
            final=True,
            created_ts=1_727_800_000.5 + seq,
            age_s=0.0,
            delivery=delivery,  # type: ignore[typeddict-item]
        ),
    )


def test_round_trip_keeps_identity_text_time_and_flow(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "state" / "pending.json")
    items = [item(1, "olá, ação ñ é \U0001f600\nsecond line"), item(2, delivery="dismissed"), item(3)]
    assert store.save(items)
    assert PendingStore(store.path).load() == items  # age_s is not stored: 0.0 both ways


def test_file_is_versioned_utf8_and_lf(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    assert store.save([item(1, "ação\nnova linha")])
    raw = store.path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    data = json.loads(raw.decode("utf-8"))
    assert data["version"] == PENDING_FILE_VERSION == 1
    (stored,) = data["items"]
    assert stored == {
        "instance": "inst-a",
        "result_seq": 1,
        "op_seq": 101,
        "kind": "ai_response",
        "flow": "claude_chat",
        "text": "ação\nnova linha",
        "final": True,
        "created_ts": 1_727_800_001.5,
        "delivery": "failed",
    }
    assert "ação" in raw.decode("utf-8")  # readable text, not \\u escapes


def test_missing_file_is_an_empty_list_and_nothing_is_written(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    assert store.load() == []
    assert not store.path.exists() and store.broken_backup is None


def test_an_empty_list_deletes_the_file(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    assert store.save([item(1)])
    assert store.save([])
    assert not store.path.exists()
    assert store.save([])  # nothing to delete: still fine


def test_only_the_newest_items_are_kept(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json", limit=3)
    assert store.save([item(seq) for seq in range(1, 8)])
    assert [i.record["result_seq"] for i in store.load()] == [5, 6, 7]
    # A file that grew by other means is capped on load too.
    store.path.write_text(dump_pending([item(seq) for seq in range(1, 8)]), encoding="utf-8")
    assert [i.record["result_seq"] for i in store.load()] == [5, 6, 7]


def test_the_default_cap_is_the_in_memory_limit(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    assert store.save([item(seq) for seq in range(PENDING_LIMIT + 5)])
    loaded = store.load()
    assert len(loaded) == PENDING_LIMIT
    assert loaded[0].record["result_seq"] == 5  # oldest dropped first, like DeliveryQueue


@pytest.mark.parametrize(
    "content",
    [
        "{ not json",
        "[1, 2, 3]",
        '{"version": 2, "items": []}',
        '{"items": []}',
        '{"version": 1, "items": {}}',
    ],
)
def test_a_broken_file_is_kept_aside_and_the_list_starts_empty(
    tmp_path: Path, content: str, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "pending.json"
    path.write_text(content, encoding="utf-8")
    store = PendingStore(path)
    with caplog.at_level(logging.ERROR, logger="app.companion.pending_store"):
        assert store.load() == []
    assert len(caplog.records) == 1  # logged once
    assert not path.exists()
    assert store.broken_backup is not None and store.broken_backup.name.startswith("pending.json.broken-")
    assert store.broken_backup.read_text(encoding="utf-8") == content
    # The next change writes a fresh file; the backup is never overwritten.
    assert store.save([item(1)])
    assert [i.record["result_seq"] for i in PendingStore(path).load()] == [1]
    assert store.broken_backup.read_text(encoding="utf-8") == content


def test_a_file_that_is_not_utf8_is_kept_aside(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    path.write_bytes(b'{"version": 1, "items": ["\xff\xfe"]}')
    store = PendingStore(path)
    assert store.load() == []
    assert store.broken_backup is not None and store.broken_backup.read_bytes().startswith(b'{"version"')


def test_a_second_broken_file_never_replaces_the_first_backup(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    path.write_text("first", encoding="utf-8")
    first = PendingStore(path)
    first.load()
    path.write_text("second", encoding="utf-8")
    second = PendingStore(path)
    second.load()
    assert first.broken_backup is not None and second.broken_backup is not None
    assert first.broken_backup != second.broken_backup
    assert first.broken_backup.read_text(encoding="utf-8") == "first"
    assert second.broken_backup.read_text(encoding="utf-8") == "second"


def test_a_broken_file_that_cannot_be_moved_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pending.json"
    path.write_text("{ not json", encoding="utf-8")

    def refuse(_src: object, _dst: object) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(pending_store_module.os, "replace", refuse)
    store = PendingStore(path)
    assert store.load() == []
    monkeypatch.undo()
    assert store.save([item(1)]) is False
    assert path.read_text(encoding="utf-8") == "{ not json"


def test_invalid_items_are_skipped_and_the_rest_restored(tmp_path: Path) -> None:
    good = json.loads(dump_pending([item(1), item(2)]))["items"]
    bad: list[object] = [
        "text only",
        {**good[0], "result_seq": "1"},
        {**good[0], "result_seq": True},
        {**good[0], "kind": "poem"},
        {**good[0], "delivery": "maybe"},
        {**good[0], "instance": ""},
        {**good[0], "text": None},
        {**good[0], "flow": 3},
        {**good[0], "created_ts": float("nan")},
    ]
    path = tmp_path / "pending.json"
    path.write_text(json.dumps({"version": 1, "items": [good[0], *bad, good[1]]}), encoding="utf-8")
    store = PendingStore(path)
    assert [i.record["result_seq"] for i in store.load()] == [1, 2]
    assert store.broken_backup is None and path.exists()


def test_a_repeated_key_is_restored_once(tmp_path: Path) -> None:
    items, skipped = parse_pending(dump_pending([item(1, "old"), item(2), item(1, "new")]))
    assert skipped == 0
    assert [(i.record["result_seq"], i.record["text"]) for i in items] == [(2, "text 2"), (1, "new")]


def test_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "pending.json"
    store = PendingStore(path)
    assert store.save([item(1)])
    before = path.read_bytes()
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def failing_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        replaced.append((os.fspath(src), os.fspath(dst)))
        raise OSError("disk full")

    monkeypatch.setattr(pending_store_module.os, "replace", failing_replace)
    assert store.save([item(1), item(2)]) is False
    # The new content went to a temp file next to the target, then os.replace; when that
    # fails the old file is intact and the temp file is gone.
    assert replaced == [(str(path.with_name("pending.json.tmp")), str(path))]
    assert path.read_bytes() == before
    assert not path.with_name("pending.json.tmp").exists()
    monkeypatch.setattr(pending_store_module.os, "replace", real_replace)
    assert store.save([item(1), item(2)])
    assert [i.record["result_seq"] for i in store.load()] == [1, 2]


def test_write_failures_never_raise_and_are_logged_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    store = PendingStore(blocker / "pending.json")
    with caplog.at_level(logging.DEBUG, logger="app.companion.pending_store"):
        assert store.save([item(1)]) is False
        assert store.save([item(1), item(2)]) is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "text 1" not in caplog.text  # never logs transcription text


def test_an_unreadable_file_is_not_fatal(tmp_path: Path) -> None:
    folder = tmp_path / "pending.json"
    folder.mkdir()  # reading a folder fails with an OSError on every platform
    store = PendingStore(folder)
    assert store.load() == []


@pytest.mark.parametrize(
    "change",
    [
        {"kind": []},
        {"kind": {"a": 1}},
        {"delivery": []},
        {"delivery": {}},
        {"instance": ["a"]},
        {"flow": []},
        {"final": "yes"},
        {"op_seq": 1.5},
        {"created_ts": 10**400},
        {"created_ts": "1.0"},
        {"created_ts": None},
    ],
)
def test_wrong_types_are_skipped_never_raised(tmp_path: Path, change: dict[str, object]) -> None:
    good = json.loads(dump_pending([item(1), item(2)]))["items"]
    path = tmp_path / "pending.json"
    path.write_text(json.dumps({"version": 1, "items": [good[0], {**good[1], **change}]}), encoding="utf-8")
    store = PendingStore(path)
    assert [i.record["result_seq"] for i in store.load()] == [1]
    assert store.broken_backup is None


@pytest.mark.parametrize(
    "content",
    [
        '{"version": 1, "items": [{"result_seq": ' + "9" * 5000 + "}]}",  # over the int digit limit
        "[" * 100_000 + "]" * 100_000,  # nesting deeper than the recursion limit
    ],
    ids=["huge-int", "deep-nesting"],
)
def test_pathological_json_is_kept_aside_never_raised(tmp_path: Path, content: str) -> None:
    path = tmp_path / "pending.json"
    path.write_text(content, encoding="utf-8")
    store = PendingStore(path)
    assert store.load() == []
    assert store.broken_backup is not None and not path.exists()


def test_a_lone_surrogate_round_trips(tmp_path: Path) -> None:
    store = PendingStore(tmp_path / "pending.json")
    items = [item(1, "broken \ud800 pair"), item(2, "ação")]
    assert store.save(items)
    store.path.read_bytes().decode("utf-8")  # still valid UTF-8
    assert PendingStore(store.path).load() == items


def test_replace_is_retried_while_the_target_is_briefly_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "pending.json"
    store = PendingStore(path)
    real_replace = os.replace
    failures = [PermissionError("held by the indexer")] * 2
    sleeps: list[float] = []

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if failures:
            raise failures.pop()
        real_replace(src, dst)

    monkeypatch.setattr(pending_store_module.os, "replace", flaky_replace)
    monkeypatch.setattr(pending_store_module.time, "sleep", sleeps.append)
    assert store.save([item(1)])
    assert sleeps == [0.05, 0.1]
    assert [i.record["result_seq"] for i in store.load()] == [1]


def test_replace_gives_up_after_a_few_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "pending.json"
    store = PendingStore(path)
    calls: list[str] = []

    def locked(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls.append(os.fspath(dst))
        raise PermissionError("locked for good")

    monkeypatch.setattr(pending_store_module.os, "replace", locked)
    monkeypatch.setattr(pending_store_module.time, "sleep", lambda _s: None)
    assert store.save([item(1)]) is False
    assert len(calls) == pending_store_module.REPLACE_ATTEMPTS
    assert not path.with_name("pending.json.tmp").exists()


def test_clearing_retries_while_the_file_is_briefly_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear list while an antivirus reads the file: the texts must not come back at the next start."""
    path = tmp_path / "pending.json"
    store = PendingStore(path)
    assert store.save([item(1)])
    real_unlink = Path.unlink
    failures = [PermissionError("held by the antivirus")] * 2
    sleeps: list[float] = []

    def flaky_unlink(self: Path, missing_ok: bool = False) -> None:
        if self == path and failures:
            raise failures.pop()
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    monkeypatch.setattr(pending_store_module.time, "sleep", sleeps.append)
    assert store.save([])
    assert sleeps == [0.05, 0.1]
    assert not path.exists()
    assert PendingStore(path).load() == []


def test_no_temp_file_with_text_is_left_behind(tmp_path: Path) -> None:
    path = tmp_path / "pending.json"
    tmp = path.with_name("pending.json.tmp")
    tmp.write_text("a write cut short by a crash", encoding="utf-8")
    store = PendingStore(path)
    assert store.load() == []
    assert not tmp.exists()  # removed at startup, even with nothing else to do
    assert store.save([item(1)])
    tmp.write_text("another one", encoding="utf-8")
    assert store.save([])  # the list emptied: the text goes with it
    assert not path.exists() and not tmp.exists()
