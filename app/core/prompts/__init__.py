"""Canonical LLM prompts (English) parameterised by `{output_lang}`."""

from __future__ import annotations

from app.core.prompts.antigravity import antigravity_system_prompt
from app.core.prompts.base import CANONICAL_VOICE_CHAT_SYSTEM_PROMPT
from app.core.prompts.claude_cli import claude_cli_system_prompt
from app.core.prompts.codex import codex_system_prompt

__all__ = [
    "CANONICAL_VOICE_CHAT_SYSTEM_PROMPT",
    "antigravity_system_prompt",
    "claude_cli_system_prompt",
    "codex_system_prompt",
]
