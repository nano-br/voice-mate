"""Installs the VoiceMate assistant skill into Claude Code and writes the settings it reads.

The skill is packaged with the companion in two languages, `skill/en/voicemate-assistant/` and
`skill/pt-BR/voicemate-assistant/` (same name and structure); `skill_source` picks one from the
`skill_language` setting (or the person's own folder, `custom_skill_dir`), and `install_skill`
copies it to `<claude home>/skills/voicemate-assistant/`. A sidecar `.voicemate-managed.json`
records the hash of every file VoiceMate wrote, so a reinstall can tell a file it may update
from one the person edited: an edited file is never overwritten, the new version goes next to
it as `<name>.new`. Switching the source updates the managed files the same way and removes the
managed ones the new source does not have. The language, name, personality, mode and the
settings the assistant may change live in `~/.voicemate/assistant.json` (`write_context`), with
the answers held in the inbox and the requests the assistant is waiting on (`pending`).
Stdlib only, no Qt.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from app.companion.assistant.session_reader import claude_home
from app.companion.contract import AnswerDelivery, AssistantMode, SkillLanguage
from app.companion.contract import ExpressionsMode as Expressions

SKILL_NAME: Final = "voicemate-assistant"
SKILL_FILE: Final = "SKILL.md"
# Bump when the packaged skill changes in a way an installed copy should know about.
SKILL_VERSION: Final = 3
SIDECAR_NAME: Final = ".voicemate-managed.json"
NEW_SUFFIX: Final = ".new"
SETTINGS_VERSION: Final = 1

PackagedLanguage = Literal["en", "pt-BR"]
PACKAGED_LANGUAGES: Final[tuple[PackagedLanguage, ...]] = ("en", "pt-BR")


@dataclass(frozen=True)
class AssistantContext:
    """What the skill reads from `assistant.json` (keys are the skill's contract)."""

    language: str  # BCP 47, e.g. "pt-BR"
    user_name: str
    personality_file: str
    expressions: Expressions
    mode: AssistantMode = "own_session"
    # The settings the assistant may change by voice (settings_catalog.py), as JSON objects.
    settings: Sequence[Mapping[str, object]] = ()
    # The answers waiting for "Hear what's ready" (inbox.py), without their text.
    inbox: Sequence[Mapping[str, object]] = ()
    notes_vault: str = ""  # the folder of the person's notes vault (Obsidian), for future use
    answer_delivery: AnswerDelivery = "announce"
    # The requests the assistant is waiting on (its follow_up actions), as JSON objects.
    pending: Sequence[Mapping[str, object]] = ()


@dataclass(frozen=True)
class SkillSource:
    """The folder `install_skill` copies. `rejected_custom` is the custom folder that was asked
    for but has no SKILL.md (the packaged skill is used instead)."""

    path: Path
    language: PackagedLanguage | None  # None for the person's own folder
    rejected_custom: str = ""


@dataclass(frozen=True)
class InstallResult:
    """`changed` is False when nothing on disk was touched; `kept_edited` lists the files the
    person edited (a `<name>.new` with the packaged version was written beside each)."""

    skill_dir: Path
    changed: bool
    written: tuple[str, ...] = field(default=())
    kept_edited: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class RemoveResult:
    removed: tuple[str, ...] = field(default=())
    kept_edited: tuple[str, ...] = field(default=())


def settings_file_path() -> Path:
    return Path.home() / ".voicemate" / "assistant.json"


def packaged_skill_dir(language: PackagedLanguage = "en") -> Path:
    """A packaged skill folder next to this module: the same relative layout in the source tree
    and in the PyInstaller bundle (the spec places it under `app/companion/assistant/skill`)."""
    return Path(__file__).resolve().parent / "skill" / language / SKILL_NAME


def packaged_language(skill_language: SkillLanguage, interface_language: str) -> PackagedLanguage:
    """`auto`: the pt-BR skill when the interface is Portuguese, the English one otherwise."""
    if skill_language == "en" or skill_language == "pt-BR":
        return skill_language
    return "pt-BR" if interface_language.lower().startswith("pt") else "en"


def skill_source(skill_language: SkillLanguage, custom_dir: str, interface_language: str) -> SkillSource:
    """The skill to install: the custom folder when it holds a SKILL.md, else a packaged one."""
    language = packaged_language(skill_language, interface_language)
    custom = custom_dir.strip()
    if custom:
        path = Path(custom).expanduser()
        if (path / SKILL_FILE).is_file():
            return SkillSource(path, None)
        return SkillSource(packaged_skill_dir(language), language, rejected_custom=custom)
    return SkillSource(packaged_skill_dir(language), language)


def _source_files(source: Path) -> list[tuple[str, Path]]:
    """(relative POSIX name, path) of every file of a skill folder, hidden ones left out."""
    found: list[tuple[str, Path]] = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_file() and not any(part.startswith(".") for part in relative.parts):
            name = relative.as_posix()
            if not name.endswith(NEW_SUFFIX):
                found.append((name, path))
    return found


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_context(ctx: AssistantContext) -> Path:
    """Atomically writes `assistant.json` (UTF-8, LF) and returns its path."""
    payload = {
        "version": SETTINGS_VERSION,
        "language": ctx.language,
        "user_name": ctx.user_name,
        "personality_file": ctx.personality_file,
        "expressions": ctx.expressions,
        "mode": ctx.mode,
        "settings": [dict(entry) for entry in ctx.settings],
        "inbox": [dict(entry) for entry in ctx.inbox],
        "pending": [dict(entry) for entry in ctx.pending],
        "notes_vault": ctx.notes_vault,
        "answer_delivery": ctx.answer_delivery,
    }
    path = settings_file_path()
    _atomic_write(path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return path


def _safe_name(name: str) -> bool:
    """A relative name inside the skill folder (a sidecar entry never reaches outside it)."""
    parts = Path(name).parts
    return bool(parts) and not Path(name).is_absolute() and ".." not in parts and ":" not in name


def _read_sidecar(skill_dir: Path) -> dict[str, str]:
    try:
        raw = json.loads((skill_dir / SIDECAR_NAME).read_text(encoding="utf-8"))
        files = raw["files"]
        if isinstance(files, dict):
            return {str(name): str(digest) for name, digest in files.items() if _safe_name(str(name))}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {}


def _sidecar_bytes(files: dict[str, str]) -> bytes:
    payload = {"skill_version": SKILL_VERSION, "files": dict(sorted(files.items()))}
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def _skill_dir(claude_home_dir: Path | None) -> Path:
    return (claude_home_dir if claude_home_dir is not None else claude_home()) / "skills" / SKILL_NAME


def install_skill(claude_home_dir: Path | None = None, source: Path | None = None) -> InstallResult:
    """Copies a skill folder (`source`, default the packaged English one) into
    `<claude home>/skills/voicemate-assistant/`.

    A file VoiceMate wrote and the person left alone is updated; one the person edited (or that
    existed before VoiceMate and differs) is kept and the new version goes to `<name>.new`. A
    managed file the source does not have is removed when the person left it alone.
    """
    skill_dir = _skill_dir(claude_home_dir)
    recorded = _read_sidecar(skill_dir)
    managed = dict(recorded)
    written: list[str] = []
    kept: list[str] = []
    changed = False
    files = _source_files(source if source is not None else packaged_skill_dir())

    wanted = {name for name, _path in files}
    for name in sorted(set(recorded) - wanted):
        stale = skill_dir / name
        if stale.is_file() and _sha256(stale.read_bytes()) == recorded[name]:
            stale.unlink()
            changed = True
        managed.pop(name, None)  # edited or gone: no longer VoiceMate's

    for name, path in files:
        packaged = path.read_bytes()
        packaged_hash = _sha256(packaged)
        dest = skill_dir / name
        if not dest.exists():
            _atomic_write(dest, packaged)
            managed[name] = packaged_hash
            written.append(name)
            changed = True
            continue
        current_hash = _sha256(dest.read_bytes())
        if current_hash == packaged_hash:
            managed[name] = packaged_hash  # up to date (or adopted)
        elif recorded.get(name) == current_hash:
            _atomic_write(dest, packaged)  # an older VoiceMate version, untouched: update it
            managed[name] = packaged_hash
            written.append(name)
            changed = True
        else:
            kept.append(name)  # edited by the person: leave it, offer the new version beside it
            sibling = skill_dir / (name + NEW_SUFFIX)
            if not sibling.exists() or sibling.read_bytes() != packaged:
                _atomic_write(sibling, packaged)
                changed = True

    sidecar = _sidecar_bytes(managed)
    sidecar_path = skill_dir / SIDECAR_NAME
    if not sidecar_path.exists() or sidecar_path.read_bytes() != sidecar:
        _atomic_write(sidecar_path, sidecar)
        changed = True
    return InstallResult(skill_dir, changed, tuple(written), tuple(kept))


def remove_skill(claude_home_dir: Path | None = None) -> RemoveResult:
    """Removes the files VoiceMate wrote and the person did not edit, then the sidecar."""
    skill_dir = _skill_dir(claude_home_dir)
    recorded = _read_sidecar(skill_dir)
    removed: list[str] = []
    kept: list[str] = []
    for name, digest in sorted(recorded.items()):
        path = skill_dir / name
        if not path.is_file():
            continue
        if _sha256(path.read_bytes()) == digest:
            path.unlink()
            removed.append(name)
        else:
            kept.append(name)
    (skill_dir / SIDECAR_NAME).unlink(missing_ok=True)
    with contextlib.suppress(OSError):
        skill_dir.rmdir()  # only succeeds when nothing else is left
    return RemoveResult(tuple(removed), tuple(kept))
