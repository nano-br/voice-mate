"""Prompt builder for the Claude Code CLI integration."""

from __future__ import annotations

from app.core.prompts.base import CANONICAL_VOICE_CHAT_SYSTEM_PROMPT


def claude_cli_system_prompt(output_lang: str = "pt-BR") -> str:
    """Return the Claude CLI system prompt with the desired output language."""
    return CANONICAL_VOICE_CHAT_SYSTEM_PROMPT.format(output_lang=output_lang)
