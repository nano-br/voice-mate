"""The conversations the assistant opens itself, and the list it picks conversations from.

A VoiceMate-owned conversation is a `stream-json` child like the central one (own_session.py):
its own working directory, name, model and first prompt, Remote Control on (so it shows in the
Desktop and on the phone), the configured permission mode and the host variables scrubbed.
VoiceMate is its only writer. The list of them lives in `conversations.json` under the state
dir (id, title, cwd, model, effort, whether it should run); a VoiceMate start resumes the ones
that were running, at most `OWNED_LIMIT` processes are alive at once (a slot is reserved under
the lock before a process starts, so parallel tool calls cannot pass the limit together), and
quit stops them all at the same time without forgetting them; nothing starts after that.

`list_entries` merges them with the Claude Desktop's conversations (transcripts whose lines say
`"entrypoint": "claude-desktop"`) for the `list_conversations` tool. Standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, Protocol

from app.companion.assistant.contract import PortListener, PortStatus
from app.companion.assistant.own_session import OwnSessionPort, SessionRecord
from app.companion.assistant.session_reader import (
    ConversationInfo,
    LiveSession,
    find_session_file,
    list_conversations,
    live_sessions,
)
from app.companion.contract import AssistantEffort, AssistantPermission, AssistantSettings

log = logging.getLogger(__name__)

OWNED_LIMIT: Final = 4  # VoiceMate-owned processes alive at once
STOP_ALL_WAIT_S: Final = 9.0  # at quit, for all of them together (each one stops in about 8 s at worst)
REGISTRY_NAME: Final = "conversations.json"
REGISTRY_VERSION: Final = 1
DESKTOP_ENTRYPOINT: Final = "claude-desktop"
HEAD_BYTES: Final = 64_000  # the first lines carry the entrypoint and the cwd
MODEL_ALIASES: Final = ("haiku", "sonnet", "opus")
EFFORTS: Final[tuple[AssistantEffort, ...]] = ("low", "medium", "high")
_SAFE_NAME_RE: Final = re.compile(r"[^a-zA-Z0-9]+")

ConversationOwner = Literal["desktop", "voicemate"]
ConversationState = Literal["busy", "idle", "waiting"]
OwnedErrorCode = Literal["unknown", "limit", "start_failed", "not_running", "invalid"]


class OwnedError(Exception):
    def __init__(self, code: OwnedErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: OwnedErrorCode = code


def valid_model(model: str) -> bool:
    """A CLI model alias or a full Claude model id."""
    return model in MODEL_ALIASES or bool(re.fullmatch(r"claude-[a-z0-9.-]+", model))


# --- persistence ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OwnedRecord:
    id: str  # the CLI session id (the transcript's file name)
    title: str
    cwd: str
    model: str
    effort: AssistantEffort
    active: bool = True  # resumed on the next VoiceMate start; False after stop_conversation
    created: float = 0.0


class OwnedRegistry:
    """`{"version": 1, "conversations": [...]}`; never raises on a bad file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[OwnedRecord]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = raw.get("conversations") if isinstance(raw, dict) else None
        out: list[OwnedRecord] = []
        for item in items if isinstance(items, list) else []:
            record = _record_from(item)
            if record is not None:
                out.append(record)
        return out

    def save(self, records: Iterable[OwnedRecord]) -> None:
        payload = json.dumps(
            {"version": REGISTRY_VERSION, "conversations": [asdict(r) for r in records]}, indent=2, ensure_ascii=False
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".conversations.", suffix=".tmp")
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
                    file.write(payload + "\n")
                tmp.replace(self.path)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        except OSError as exc:
            log.warning("assistant: cannot save %s (%s)", self.path, exc)


def _record_from(item: object) -> OwnedRecord | None:
    if not isinstance(item, dict):
        return None
    sid, title, cwd, model = item.get("id"), item.get("title"), item.get("cwd"), item.get("model")
    if not all(isinstance(v, str) and v for v in (sid, title, cwd, model)):
        return None
    effort = item.get("effort")
    created = item.get("created")
    return OwnedRecord(
        id=str(sid),
        title=str(title),
        cwd=str(cwd),
        model=str(model),
        effort=effort if effort in EFFORTS else "medium",
        active=item.get("active") is not False,
        created=float(created) if isinstance(created, int | float) else 0.0,
    )


# --- the ports -----------------------------------------------------------------------------


class OwnedPort(Protocol):
    """What the manager needs from one conversation's process (OwnSessionPort, a fake in tests)."""

    def start(self, on_change: PortListener) -> None: ...

    def send(self, text: str) -> bool: ...

    def status(self) -> PortStatus: ...

    def stop(self) -> None: ...

    def set_model(self, model: str) -> bool: ...


@dataclass(frozen=True)
class PortSpec:
    """How to start one conversation's process."""

    record: OwnedRecord
    first_prompt: str | None  # None: resume and wait for the next prompt
    permission_mode: AssistantPermission
    store: _RecordStore


PortFactory = Callable[[PortSpec], OwnedPort]


class _RecordStore:
    """The `SessionStore` of one owned conversation, backed by the registry: when the CLI ends up
    on another session id (the old one could not be resumed), the record follows it."""

    def __init__(self, manager: OwnedConversations, conversation_id: str, *, resume: bool) -> None:
        self._manager = manager
        self.conversation_id = conversation_id
        self._resume = resume  # False for a new conversation: nothing to resume yet

    def load(self) -> SessionRecord | None:
        if not self._resume:
            return None
        record = self._manager.record(self.conversation_id)
        return SessionRecord(record.id, "", record.cwd) if record is not None else None

    def save(self, record: SessionRecord) -> None:
        if record.session_id != self.conversation_id:
            self._manager._rename(self.conversation_id, record.session_id)
            self.conversation_id = record.session_id

    def clear(self) -> None:
        pass


def default_port_factory(spec: PortSpec) -> OwnedPort:
    record = spec.record

    def new_session_id() -> str:
        # The record's id while it has no transcript yet (a new conversation); a fresh one when
        # the old session exists but could not be resumed (another process owns it).
        return record.id if find_session_file(record.id) is None else str(uuid.uuid4())

    return OwnSessionPort(
        AssistantSettings(mode="own_session"),
        cwd=Path(record.cwd),
        store=spec.store,
        first_prompt=spec.first_prompt,
        session_name=record.title,
        model=record.model,
        effort=record.effort,
        permission_mode=spec.permission_mode,
        new_session_id=new_session_id,
    )


def _safe_folder_name(title: str) -> str:
    return _SAFE_NAME_RE.sub("-", title).strip("-").lower()[:40] or "conversation"


class OwnedConversations:
    """The VoiceMate-owned conversations. Every method may be called from any thread; `start`,
    `send` and `stop` block only while a process starts or stops."""

    def __init__(
        self,
        root: Path,
        *,
        permission_mode: AssistantPermission = "acceptEdits",
        port_factory: PortFactory = default_port_factory,
        clock: Callable[[], float] = time.time,
        new_id: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self.root = root
        self._registry = OwnedRegistry(root / REGISTRY_NAME)
        self._permission_mode: AssistantPermission = permission_mode
        self._factory = port_factory
        self._clock = clock
        self._new_id = new_id
        self._lock = threading.RLock()
        self._records: dict[str, OwnedRecord] = {r.id: r for r in self._registry.load()}
        self._ports: dict[str, OwnedPort] = {}
        self._starting: set[str] = set()  # ids whose process is starting (a reserved slot each)
        self._closed = False  # stop_all ran: nothing starts any more

    # --- queries -------------------------------------------------------------------------------

    def records(self) -> list[OwnedRecord]:
        with self._lock:
            return list(self._records.values())

    def record(self, conversation_id: str) -> OwnedRecord | None:
        with self._lock:
            return self._records.get(conversation_id)

    def is_owned(self, conversation_id: str) -> bool:
        return self.record(conversation_id) is not None

    def alive(self, conversation_id: str) -> bool:
        with self._lock:
            port = self._ports.get(conversation_id)
        return port is not None and port.status().state in ("starting", "ready", "busy")

    def state(self, conversation_id: str) -> ConversationState:
        with self._lock:
            port = self._ports.get(conversation_id)
        status = port.status().state if port is not None else "stopped"
        return "busy" if status in ("starting", "busy") else "idle"

    # --- settings ------------------------------------------------------------------------------

    def set_permission_mode(self, mode: AssistantPermission) -> None:
        """Used by the conversations started from now on (running ones keep theirs)."""
        with self._lock:
            self._permission_mode = mode

    # --- lifecycle -----------------------------------------------------------------------------

    def resume_all(self) -> list[str]:
        """Resume the conversations that were running at the last quit (newest first, up to the
        limit), without a prompt. Returns their ids."""
        with self._lock:
            wanted = sorted((r for r in self._records.values() if r.active), key=lambda r: r.created, reverse=True)
        resumed: list[str] = []
        for record in wanted[:OWNED_LIMIT]:
            try:
                self._reserve(record.id)
            except OwnedError as exc:
                log.warning("assistant: cannot resume %s (%s)", record.id, exc)
                if exc.code == "not_running" and self._closed:
                    break
                continue
            try:
                resumed.append(self._run(record, None))
            except OwnedError as exc:
                log.warning("assistant: cannot resume %s (%s)", record.id, exc)
            finally:
                self._release(record.id)
        return resumed

    def start(self, title: str, prompt: str, model: str = "sonnet", effort: str = "medium", cwd: str = "") -> str:
        """Start a new conversation whose first prompt is `prompt`; returns its id."""
        title, prompt = title.strip(), prompt.strip()
        if not title or not prompt:
            raise OwnedError("invalid", "title and prompt must not be empty")
        if not valid_model(model):
            raise OwnedError("invalid", f"unknown model {model!r}: use haiku, sonnet, opus or a claude-... model id")
        effort_value = next((e for e in EFFORTS if e == effort), None)
        if effort_value is None:
            raise OwnedError("invalid", f"unknown effort {effort!r}: use low, medium or high")
        chosen = Path(cwd.strip()).expanduser() if cwd.strip() else None
        if chosen is not None and not chosen.is_dir():
            raise OwnedError("invalid", f"the folder {chosen} does not exist")
        conversation_id = self._new_id()
        self._reserve(conversation_id)
        try:
            return self._start_reserved(conversation_id, title, prompt, model, effort_value, chosen)
        finally:
            self._release(conversation_id)

    def _start_reserved(
        self, conversation_id: str, title: str, prompt: str, model: str, effort: AssistantEffort, chosen: Path | None
    ) -> str:
        folder = chosen or self.root / "work" / f"{_safe_folder_name(title)}-{conversation_id[:8]}"
        record = OwnedRecord(
            id=conversation_id,
            title=title,
            cwd=str(folder),
            model=model,
            effort=effort,
            active=True,
            created=self._clock(),
        )
        with self._lock:
            self._records[record.id] = record
            self._save()
        try:
            return self._run(record, prompt, resume=False)
        except OwnedError:
            with self._lock:  # never started: nothing to resume later
                self._records.pop(record.id, None)
                self._save()
            raise

    def send(self, conversation_id: str, text: str) -> None:
        """Write a prompt; a conversation that is not running is resumed with it."""
        record = self._require(conversation_id)
        with self._lock:
            port = self._ports.get(record.id)
        if port is not None and self.alive(record.id):
            if not port.send(text):
                raise OwnedError("not_running", f"the conversation {record.title!r} is not accepting prompts")
            return
        self._reserve(record.id)
        try:
            self._update(replace(record, active=True))
            self._run(record, text)
        finally:
            self._release(record.id)

    def set_model(self, conversation_id: str, model: str) -> None:
        record = self._require(conversation_id)
        if not valid_model(model):
            raise OwnedError("invalid", f"unknown model {model!r}: use haiku, sonnet, opus or a claude-... model id")
        with self._lock:
            port = self._ports.get(record.id)
        if port is not None and self.alive(record.id) and not port.set_model(model):
            raise OwnedError("not_running", f"the conversation {record.title!r} is not running")
        self._update(replace(record, model=model))  # the next resume starts with it

    def stop(self, conversation_id: str) -> None:
        """Stop the process and do not resume it on the next start (a prompt still resumes it)."""
        record = self._require(conversation_id)
        self._update(replace(record, active=False))
        with self._lock:
            port = self._ports.pop(record.id, None)
        if port is not None:
            port.stop()

    def stop_all(self, wait_s: float = STOP_ALL_WAIT_S) -> None:
        """BLOCKING (`wait_s` at most): at quit. All of them stop at the same time; a process
        still starting is stopped as soon as it is up, and nothing starts after this. The
        records stay active, so the next start resumes them."""
        with self._lock:
            self._closed = True
            ports = list(self._ports.values())
            self._ports.clear()
        threads = [threading.Thread(target=self._stop_port, args=(port,), daemon=True) for port in ports]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + wait_s
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            log.warning("assistant: some conversations did not stop in %.0f s", wait_s)

    @staticmethod
    def _stop_port(port: OwnedPort) -> None:
        try:
            port.stop()
        except Exception:  # noqa: BLE001 - stop the others anyway
            log.exception("assistant: stopping a conversation failed")

    # --- internals -----------------------------------------------------------------------------

    def _require(self, conversation_id: str) -> OwnedRecord:
        record = self.record(conversation_id)
        if record is None:
            raise OwnedError("unknown", f"no VoiceMate conversation has the id {conversation_id}")
        return record

    def _reserve(self, conversation_id: str) -> None:
        """Take a process slot for `conversation_id` (check and take under one lock)."""
        with self._lock:
            if self._closed:
                raise OwnedError("not_running", "VoiceMate is quitting")
            if conversation_id in self._starting:
                raise OwnedError("not_running", "that conversation is starting already")
            running = sum(1 for i in self._ports if i != conversation_id and self.alive(i))
            if running + len(self._starting) >= OWNED_LIMIT:
                raise OwnedError("limit", f"{OWNED_LIMIT} conversations are already running: stop one first")
            self._starting.add(conversation_id)

    def _release(self, conversation_id: str) -> None:
        with self._lock:
            self._starting.discard(conversation_id)

    def _run(self, record: OwnedRecord, first_prompt: str | None, *, resume: bool = True) -> str:
        with self._lock:
            old = self._ports.pop(record.id, None)
            mode = self._permission_mode
        if old is not None:
            old.stop()
        store = _RecordStore(self, record.id, resume=resume)
        port = self._factory(PortSpec(record, first_prompt, mode, store))
        port.start(lambda _status: None)
        status = port.status()
        if status.state == "error":
            raise OwnedError("start_failed", f"the conversation did not start: {status.error} {status.detail}".strip())
        conversation_id = store.conversation_id
        with self._lock:
            closed = self._closed
            if not closed:
                self._ports[conversation_id] = port
        if closed:  # VoiceMate quit while it started: no child outlives it
            self._stop_port(port)
            raise OwnedError("not_running", "VoiceMate is quitting")
        log.info(
            "assistant: conversation %s (%s) %s",
            conversation_id,
            record.title,
            "started" if first_prompt else "resumed",
        )
        return conversation_id

    def _rename(self, old_id: str, new_id: str) -> None:
        with self._lock:
            record = self._records.pop(old_id, None)
            if record is None:
                return
            self._records[new_id] = replace(record, id=new_id)
            port = self._ports.pop(old_id, None)
            if port is not None:
                self._ports[new_id] = port
            self._save()
        log.info("assistant: conversation %s continues as %s", old_id, new_id)

    def _update(self, record: OwnedRecord) -> None:
        with self._lock:
            if record.id in self._records:
                self._records[record.id] = record
                self._save()

    def _save(self) -> None:
        self._registry.save(self._records.values())


# --- the list the assistant picks from -----------------------------------------------------


@dataclass(frozen=True)
class ConversationEntry:
    id: str
    title: str
    owner: ConversationOwner
    state: ConversationState
    last_activity: str  # ISO 8601, UTC
    cwd: str

    def as_json(self) -> dict[str, str]:
        return asdict(self)


def transcript_head(path: Path) -> tuple[str, str]:
    """`(entrypoint, cwd)` from the first lines of a transcript ("" when absent)."""
    try:
        with open(path, "rb") as file:
            head = file.read(HEAD_BYTES)
    except OSError:
        return "", ""
    entrypoint = cwd = ""
    for raw in head.split(b"\n"):
        if b'"entrypoint"' not in raw and b'"cwd"' not in raw:
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        found_entry, found_cwd = line.get("entrypoint"), line.get("cwd")
        entrypoint = entrypoint or (found_entry if isinstance(found_entry, str) else "")
        cwd = cwd or (found_cwd if isinstance(found_cwd, str) else "")
        if entrypoint and cwd:
            break
    return entrypoint, cwd


def _iso(timestamp: float) -> str:
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def _live_state(live: LiveSession | None) -> ConversationState:
    if live is None or live.status == "idle":
        return "idle"
    return "busy" if live.status == "busy" else "waiting"


def list_entries(
    owned: OwnedConversations,
    limit: int = 20,
    *,
    exclude: Iterable[str] = (),
    lister: Callable[[int], list[ConversationInfo]] = list_conversations,
    sessions: Callable[[], dict[str, LiveSession]] = live_sessions,
    head: Callable[[Path], tuple[str, str]] = transcript_head,
) -> list[ConversationEntry]:
    """Desktop and VoiceMate-owned conversations, newest activity first. `exclude` drops ids
    (the central conversation itself)."""
    limit = max(1, min(limit, 100))
    skip = set(exclude)
    live = sessions()
    entries: dict[str, ConversationEntry] = {}
    # Many transcripts are not Desktop ones (labs, CLI runs): scan more than asked.
    for info in lister(limit * 4 + 20):
        if info.session_id in skip:
            continue
        record = owned.record(info.session_id)
        if record is not None:
            entries[info.session_id] = ConversationEntry(
                record.id, record.title, "voicemate", owned.state(record.id), _iso(info.modified), record.cwd
            )
            continue
        entrypoint, cwd = head(info.path)
        if entrypoint != DESKTOP_ENTRYPOINT:
            continue
        title = info.title or (live[info.session_id].name if info.session_id in live else "")
        entries[info.session_id] = ConversationEntry(
            info.session_id, title, "desktop", _live_state(live.get(info.session_id)), _iso(info.modified), cwd
        )
    for record in owned.records():
        if record.id not in entries and record.id not in skip:
            entries[record.id] = ConversationEntry(
                record.id, record.title, "voicemate", owned.state(record.id), _iso(record.created), record.cwd
            )
    ordered = sorted(entries.values(), key=lambda e: e.last_activity, reverse=True)
    return ordered[:limit]
