"""The Not copied list on disk, so it survives a Quit or a crash of the companion.

A result lands in that list after the companion already ACKed it to the daemon
(`failed`, `dismissed`) or after its daemon instance went away: the daemon will never
offer it again, so without this file a restart would lose it for good.

File: `paths.pending_path()` (Windows `%LOCALAPPDATA%\\VoiceMate\\pending.json`, Linux
`${XDG_STATE_HOME:-~/.local/state}/voicemate/pending.json`). It holds transcription
text: it stays in the user profile, is deleted once the list is empty, and the Windows
uninstaller removes it. Schema (UTF-8, LF, items oldest first):

    {"version": 1, "items": [{"instance": "...", "result_seq": 7, "op_seq": 7,
      "kind": "transcript", "flow": "clipboard", "text": "...", "final": true,
      "created_ts": 1727800000.0, "delivery": "failed"}]}

`instance` + `result_seq` are the identity the delivery queue and `copy_result` use;
`age_s` is not stored (the daemon computes it per response). Only the newest `limit`
items are kept. Never raises (`load` runs while the app starts): a file that cannot be
parsed is moved aside to `pending.json.broken-<YYYYmmdd-HHMMSS>` (like a broken
companion.toml) and the list starts empty; an invalid item is skipped; a failed write is
logged and the app goes on with the list in memory. `pending.json.tmp` (a write cut short
by a crash) is removed at load and whenever the list is emptied: it holds text too.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final, TypeGuard, cast, get_args

from app.companion.contract import RecentItem
from app.companion.delivery import PENDING_LIMIT
from app.protocol.models import DeliveryStatus, ResultKind, ResultRecord

log = logging.getLogger(__name__)

PENDING_FILE_VERSION: Final = 1
BROKEN_SUFFIX: Final = ".broken"
TMP_SUFFIX: Final = ".tmp"
# Windows: os.replace and unlink fail while another process briefly holds the target open
# (an antivirus or the search indexer): try this many times, waiting 50 ms more each time.
REPLACE_ATTEMPTS: Final = 5
REPLACE_BACKOFF_S: Final = 0.05
_KINDS: Final = frozenset(get_args(ResultKind))
_STATUSES: Final = frozenset(get_args(DeliveryStatus))


def _retry_while_shared(action: Callable[[], None]) -> None:
    """Run a file operation, retrying while Windows reports the file as in use."""
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            action()
            return
        except PermissionError:  # Windows: the file is briefly open (antivirus, indexer)
            if attempt == REPLACE_ATTEMPTS:
                raise
            time.sleep(REPLACE_BACKOFF_S * attempt)


class PendingFileError(ValueError):
    """The file is not a pending list this version can read."""


def item_to_json(item: RecentItem) -> dict[str, Any]:
    record = item.record
    return {
        "instance": item.instance,
        "result_seq": record["result_seq"],
        "op_seq": record["op_seq"],
        "kind": record["kind"],
        "flow": record["flow"],
        "text": record["text"],
        "final": record["final"],
        "created_ts": record["created_ts"],
        "delivery": record["delivery"],
    }


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _timestamp(value: object) -> float | None:
    """A finite float, or None (a bool, a string, NaN, or an int too large for a float)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def item_from_json(raw: object) -> RecentItem | None:
    """One stored item, or None when it is not a valid one (skipped, never fatal).

    Every field is type-checked before it is used, so no JSON value can raise here."""
    if not isinstance(raw, dict):
        return None
    instance, result_seq, op_seq = raw.get("instance"), raw.get("result_seq"), raw.get("op_seq")
    kind, flow, text, final = raw.get("kind"), raw.get("flow"), raw.get("text"), raw.get("final")
    delivery = raw.get("delivery", "failed")
    created_ts = _timestamp(raw.get("created_ts", 0.0))
    if not isinstance(instance, str) or not instance or not isinstance(text, str) or not isinstance(final, bool):
        return None
    if not _is_int(result_seq) or not _is_int(op_seq) or created_ts is None:
        return None
    if not isinstance(kind, str) or kind not in _KINDS or not isinstance(delivery, str) or delivery not in _STATUSES:
        return None
    if flow is not None and not isinstance(flow, str):
        return None
    record = ResultRecord(
        result_seq=result_seq,
        op_seq=op_seq,
        kind=cast(ResultKind, kind),
        flow=flow,
        text=text,
        final=final,
        created_ts=created_ts,
        age_s=0.0,
        delivery=cast(DeliveryStatus, delivery),
    )
    return RecentItem(instance, record)


def parse_pending(text: str, limit: int = PENDING_LIMIT) -> tuple[list[RecentItem], int]:
    """(items oldest first, newest `limit` only, one per key; number of invalid items skipped).

    Raises PendingFileError when the text is not a version 1 pending list at all."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:  # bad JSON, an integer over the digit limit, deep nesting
        raise PendingFileError(f"not valid JSON ({exc.__class__.__name__}: {exc})") from exc
    if not isinstance(data, dict):
        raise PendingFileError("not a JSON object")
    version = data.get("version")
    if version != PENDING_FILE_VERSION:
        raise PendingFileError(f"unsupported version {version!r}")
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise PendingFileError("`items` is not a list")
    by_key: dict[tuple[str, int], RecentItem] = {}
    skipped = 0
    for raw in raw_items:
        try:
            item = item_from_json(raw)
        except (TypeError, ValueError, OverflowError):  # a safety net: item_from_json checks every type
            item = None
        if item is None:
            skipped += 1
            continue
        key = (item.instance, item.record["result_seq"])
        by_key.pop(key, None)  # a repeated key keeps its last position
        by_key[key] = item
    items = list(by_key.values())
    return items[-limit:] if limit > 0 else [], skipped


def dump_pending(items: Sequence[RecentItem]) -> str:
    payload = {"version": PENDING_FILE_VERSION, "items": [item_to_json(item) for item in items]}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate (JSON can carry one): \u escapes keep the file valid UTF-8.
        text = json.dumps(payload, ensure_ascii=True, indent=2) + "\n"
    return text


class PendingStore:
    """Reads the list once at startup (`load`) and rewrites it whole on every change (`save`).

    `save` is meant for one background thread; both methods are thread-safe anyway."""

    def __init__(self, path: Path, *, limit: int = PENDING_LIMIT) -> None:
        self._path = path
        self._limit = limit
        self._lock = threading.Lock()
        self._writable = True
        self._failing = False
        self.broken_backup: Path | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def _tmp(self) -> Path:
        return self._path.with_name(self._path.name + TMP_SUFFIX)

    def load(self) -> list[RecentItem]:
        """The stored items, oldest first. Missing file: empty. Broken file: kept aside, empty.

        Never raises: it runs in the controller's constructor, at every start."""
        with self._lock:
            self._remove_tmp()  # a write cut short; pending.json itself is whole (os.replace)
            try:
                text = self._path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return []
            except (OSError, UnicodeDecodeError) as exc:
                self._set_aside(f"unreadable ({exc.__class__.__name__}: {exc})")
                return []
            try:
                items, skipped = parse_pending(text, self._limit)
            except PendingFileError as exc:
                self._set_aside(str(exc))
                return []
            except Exception as exc:  # noqa: BLE001 - a last resort: never stop the app from starting
                self._set_aside(f"not readable ({exc.__class__.__name__}: {exc})")
                return []
            if skipped:
                log.warning("pending list: %s invalid item(s) in %s skipped", skipped, self._path)
            if items:
                log.info("pending list: %s not copied transcription(s) restored from %s", len(items), self._path)
            return items

    def save(self, items: Sequence[RecentItem]) -> bool:
        """Write the newest `limit` items (oldest first); an empty list deletes the file.

        Never raises: False when it could not be written (logged once until it works again)."""
        kept = list(items)[-self._limit :] if self._limit > 0 else []
        with self._lock:
            if not self._writable:
                return False
            try:
                if kept:
                    self._write(dump_pending(kept))
                else:
                    # The cleared texts must not come back at the next start: retry like _write.
                    _retry_while_shared(lambda: self._path.unlink(missing_ok=True))
                    _retry_while_shared(lambda: self._tmp.unlink(missing_ok=True))  # no text may stay behind
            except OSError as exc:
                if not self._failing:
                    log.warning("pending list: cannot write %s (%s); keeping it in memory only", self._path, exc)
                else:
                    log.debug("pending list: still cannot write %s (%s)", self._path, exc)
                self._failing = True
                return False
            if self._failing:
                log.info("pending list: %s is writable again", self._path)
                self._failing = False
            return True

    def _remove_tmp(self) -> None:
        try:
            self._tmp.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("pending list: cannot remove %s (%s)", self._tmp, exc)

    def _write(self, text: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._tmp
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            _retry_while_shared(lambda: os.replace(tmp, self._path))
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise

    def _set_aside(self, problem: str) -> None:
        """Keep a file we cannot read (never overwrite it): moved to `<name>.broken-<stamp>`.
        When even that fails, nothing is written to it for the rest of the session."""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self._path.with_name(f"{self._path.name}{BROKEN_SUFFIX}-{stamp}")
        counter = 1
        while backup.exists():
            backup = self._path.with_name(f"{self._path.name}{BROKEN_SUFFIX}-{stamp}-{counter}")
            counter += 1
        try:
            os.replace(self._path, backup)
        except OSError as exc:
            log.error(
                "pending list: %s is %s and could not be moved aside (%s); not writing to it",
                self._path,
                problem,
                exc,
            )
            self._writable = False
            return
        log.error("pending list: %s is %s; kept as %s, starting with an empty list", self._path, problem, backup)
        self.broken_backup = backup
