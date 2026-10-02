"""Setup yes/no prompt: the answer is accepted in all three UI languages (pt-BR, en, es)."""

from __future__ import annotations

import pytest

from app.setup import gpu_bootstrap


@pytest.mark.parametrize("answer", ["s", "sim", "si", "sí", "y", "yes", "SÍ", "Yes"])
def test_yes_in_any_language(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(gpu_bootstrap, "_ask", lambda _prompt: answer)
    assert gpu_bootstrap._prompt_yes_no("Install?", default_yes=False, interactive=True) is True


@pytest.mark.parametrize("answer", ["n", "no", "não", "nao"])
def test_anything_else_is_no(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(gpu_bootstrap, "_ask", lambda _prompt: answer)
    assert gpu_bootstrap._prompt_yes_no("Install?", default_yes=True, interactive=True) is False


def test_empty_answer_uses_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_bootstrap, "_ask", lambda _prompt: "")
    assert gpu_bootstrap._prompt_yes_no("Install?", default_yes=True, interactive=True) is True
    assert gpu_bootstrap._prompt_yes_no("Install?", default_yes=False, interactive=True) is False
