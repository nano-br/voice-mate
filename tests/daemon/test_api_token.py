"""API token: a 0600 file created on first use, the env override, bearer matching."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from app.daemon.auth import TOKEN_ENV, bearer_matches, default_token_path, load_or_create_token

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def test_default_path_is_in_the_voicemate_config_dir() -> None:
    assert default_token_path().parts[-3:] == (".config", "voicemate", "api-token")


@posix_only
def test_creates_a_private_urlsafe_token_once(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "api-token"
    token = load_or_create_token(path, env={})
    assert len(token) == 43  # 32 random bytes, urlsafe base64 without padding
    assert set(token) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == token + "\n"
    assert load_or_create_token(path, env={}) == token  # reused, not regenerated


@posix_only
def test_existing_file_is_used_and_its_permissions_tightened(tmp_path: Path) -> None:
    path = tmp_path / "api-token"
    path.write_text("my-token\n", encoding="utf-8")
    os.chmod(path, 0o644)
    assert load_or_create_token(path, env={}) == "my-token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_an_empty_file_gets_a_fresh_token(tmp_path: Path) -> None:
    path = tmp_path / "api-token"
    path.write_text("", encoding="utf-8")
    token = load_or_create_token(path, env={})
    assert token and path.read_text(encoding="utf-8").strip() == token


def test_env_override_wins_and_touches_no_file(tmp_path: Path) -> None:
    path = tmp_path / "api-token"
    assert load_or_create_token(path, env={TOKEN_ENV: " test-token "}) == "test-token"
    assert not path.exists()


@pytest.mark.parametrize(
    ("header", "ok"),
    [
        ("Bearer secret", True),
        ("bearer secret", True),
        ("Bearer  secret ", True),
        ("Bearer wrong", False),
        ("Basic secret", False),
        ("secret", False),
        ("", False),
        (None, False),
    ],
)
def test_bearer_matches(header: str | None, ok: bool) -> None:
    assert bearer_matches(header, "secret") is ok
