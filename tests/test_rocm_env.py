from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.rocm_env import configure_rocm_env

_KEYS = (
    "MIOPEN_FIND_MODE",
    "MIOPEN_USER_DB_PATH",
    "PYTORCH_TUNABLEOP_ENABLED",
    "PYTORCH_TUNABLEOP_FILENAME",
    "PYTORCH_TUNABLEOP_MAX_TUNING_DURATION_MS",
    "PYTORCH_TUNABLEOP_MAX_TUNING_ITERATIONS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)


def test_sets_miopen_and_tunableop_for_amd(tmp_path: Path) -> None:
    configure_rocm_env("amd", cache_dir=tmp_path)
    assert os.environ["MIOPEN_FIND_MODE"] == "FAST"
    assert os.environ["MIOPEN_USER_DB_PATH"] == str(tmp_path / "miopen")
    assert os.environ["PYTORCH_TUNABLEOP_ENABLED"] == "1"
    assert os.environ["PYTORCH_TUNABLEOP_FILENAME"] == str(tmp_path / "tunableop.csv")


def test_noop_for_non_amd(tmp_path: Path) -> None:
    configure_rocm_env("nvidia", cache_dir=tmp_path)
    configure_rocm_env("cpu", cache_dir=tmp_path)
    for key in _KEYS:
        assert key not in os.environ


def test_respects_user_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIOPEN_FIND_MODE", "NORMAL")
    configure_rocm_env("amd", cache_dir=tmp_path)
    assert os.environ["MIOPEN_FIND_MODE"] == "NORMAL"  # setdefault does not overwrite
