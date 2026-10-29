"""Where the assistant's personality comes from, and the text the person writes for it.

`personality_source` picks it: "none", "file" (a Markdown file of the person's, for example an
Obsidian note) or "custom" (a text written in the Assistant tab, kept by VoiceMate in a UTF-8
file under its state dir, never inside companion.toml). The skill reads one path either way:
`personality_file` in assistant.json (`effective_file`). `obsidian_vaults` finds the person's
vaults, so the Assistant tab can suggest one and the file picker can start there; `vault_problem`
says why a chosen vault folder looks wrong (a warning, never a refusal). Standard library only.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Final

from app.companion import paths
from app.companion.contract import AssistantSettings

log = logging.getLogger(__name__)

CUSTOM_LIMIT: Final = 20_000  # characters of a written personality


def read_custom(path: Path | None = None) -> str:
    """The written personality ("" when there is none or it cannot be read)."""
    path = path or paths.assistant_personality_path()
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("assistant: cannot read the personality %s (%s)", path, exc)
        return ""


def save_custom(text: str, path: Path | None = None) -> None:
    """Keep the written personality (raises OSError, ValueError when it is too long)."""
    if len(text) > CUSTOM_LIMIT:
        raise ValueError(f"the personality is longer than {CUSTOM_LIMIT} characters")
    path = path or paths.assistant_personality_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".personality.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as file:
            file.write(text.replace("\r\n", "\n"))
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def effective_file(settings: AssistantSettings, custom_path: Path | None = None) -> str:
    """The `personality_file` of assistant.json: the chosen file, the written text's file, or ""."""
    if settings.personality_source == "file":
        return settings.personality_file
    if settings.personality_source == "custom":
        path = custom_path or paths.assistant_personality_path()
        return str(path) if read_custom(path).strip() else ""
    return ""


def obsidian_vaults(appdata: str | None = None) -> list[Path]:
    """The Obsidian vault folders that exist (from %APPDATA%\\obsidian\\obsidian.json), newest first."""
    base = appdata if appdata is not None else os.environ.get("APPDATA", "")
    if not base:
        return []
    try:
        raw = json.loads((Path(base) / "obsidian" / "obsidian.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    vaults = raw.get("vaults") if isinstance(raw, dict) else None
    if not isinstance(vaults, dict):
        return []
    found: list[tuple[float, Path]] = []
    for entry in vaults.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        stamp = entry.get("ts")
        path = Path(entry["path"])
        try:
            if path.is_dir():
                found.append((float(stamp) if isinstance(stamp, int | float) else 0.0, path))
        except OSError:
            continue
    return [path for _stamp, path in sorted(found, key=lambda item: item[0], reverse=True)]


def vault_problem(folder: str) -> str:
    """Empty for an empty or plausible vault folder, else "missing" or "no_obsidian" (no .obsidian inside)."""
    if not folder:
        return ""
    path = Path(folder)
    try:
        if not path.is_dir():
            return "missing"
        return "" if (path / ".obsidian").is_dir() else "no_obsidian"
    except OSError:
        return "missing"
