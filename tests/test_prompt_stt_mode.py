"""Setup question for the default speech-to-text mode."""

from __future__ import annotations

import pytest

from app.setup import gpu_bootstrap


@pytest.mark.parametrize(("answer", "mode"), [("1", "auto"), ("2", "gpu"), ("3", "cpu"), ("CPU", "cpu"), ("", "gpu")])
def test_answers_map_to_modes(monkeypatch: pytest.MonkeyPatch, answer: str, mode: str) -> None:
    monkeypatch.setattr(gpu_bootstrap, "_ask", lambda _prompt: answer)
    assert gpu_bootstrap._prompt_stt_mode("amd", "gpu", interactive=True) == mode


def test_without_a_gpu_or_a_terminal_nothing_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_bootstrap, "_ask", lambda _prompt: pytest.fail("asked"))
    assert gpu_bootstrap._prompt_stt_mode("cpu", "gpu", interactive=True) == "cpu"
    assert gpu_bootstrap._prompt_stt_mode("nvidia", "auto", interactive=False) == "auto"
