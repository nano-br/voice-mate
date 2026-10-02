"""Bearer token of the daemon API (`--api-token`).

Single source: `~/.config/voicemate/api-token`, created on first use with mode
0600 (32 random bytes, urlsafe base64). The companion reads the same file (via
`wsl.exe ... cat` on Windows). `VOICEMATE_API_TOKEN` overrides it, for tests only.
With a token, every endpoint but `/health` requires `Authorization: Bearer <token>`.
"""

from __future__ import annotations

import contextlib
import hmac
import os
import secrets
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Final

TOKEN_ENV: Final = "VOICEMATE_API_TOKEN"
TOKEN_BYTES: Final = 32
# How long to wait for another starting engine to finish writing the token file it
# just created before treating it as empty.
_RACE_WAIT_S: Final = 1.0


def default_token_path() -> Path:
    return Path.home() / ".config" / "voicemate" / "api-token"


def load_or_create_token(path: Path | None = None, env: Mapping[str, str] | None = None) -> str:
    """The token in force: the env override, else the file (created when absent or empty)."""
    environ = os.environ if env is None else env
    override = environ.get(TOKEN_ENV, "").strip()
    if override:
        return override
    target = path if path is not None else default_token_path()
    existing = _read(target)
    if existing:
        _restrict(target)
        return existing
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Created concurrently (another engine starting): use theirs, giving it a moment
        # to write the token; only a file that stays empty gets overwritten.
        existing = _read_soon(target)
        if existing:
            return existing
        fd = os.open(target, os.O_WRONLY | os.O_TRUNC)
    with os.fdopen(fd, "w", encoding="ascii", newline="\n") as handle:
        handle.write(token + "\n")
    _restrict(target)
    return token


def bearer_matches(header: str | None, token: str) -> bool:
    """True when `header` is `Bearer <token>` (constant-time comparison)."""
    if not header:
        return False
    scheme, _sep, value = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(value.strip().encode("utf-8"), token.encode("utf-8"))


def _read_soon(path: Path) -> str:
    deadline = time.monotonic() + _RACE_WAIT_S
    while True:
        existing = _read(path)
        if existing or time.monotonic() >= deadline:
            return existing
        time.sleep(0.05)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _restrict(path: Path) -> None:
    """Owner read/write only (a copied or hand-made file may be world-readable)."""
    with contextlib.suppress(OSError):
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            path.chmod(0o600)
