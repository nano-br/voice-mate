from __future__ import annotations

import pytest

from app.core.prompts import (
    CANONICAL_VOICE_CHAT_SYSTEM_PROMPT,
    antigravity_system_prompt,
    claude_cli_system_prompt,
    codex_system_prompt,
)


def test_canonical_prompt_is_in_english_with_placeholder() -> None:
    text = CANONICAL_VOICE_CHAT_SYSTEM_PROMPT
    assert "{output_lang}" in text
    assert "voice conversation" in text
    assert "text-to-speech" in text
    assert "Whisper" in text


def test_claude_cli_default_injects_pt_br() -> None:
    rendered = claude_cli_system_prompt()
    assert "{output_lang}" not in rendered
    assert "Always reply in pt-BR" in rendered


def test_claude_cli_injects_arbitrary_language() -> None:
    rendered = claude_cli_system_prompt("en")
    assert "Always reply in en" in rendered

    rendered_es = claude_cli_system_prompt("es")
    assert "Always reply in es" in rendered_es


def test_codex_stub_raises_until_implemented() -> None:
    with pytest.raises(NotImplementedError):
        codex_system_prompt()


def test_antigravity_stub_raises_until_implemented() -> None:
    with pytest.raises(NotImplementedError):
        antigravity_system_prompt()
