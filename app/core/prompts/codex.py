"""Prompt builder stub for the Codex / OpenAI integration.

Implement when adding the Codex backend in `app/features/codex/`.
"""

from __future__ import annotations


def codex_system_prompt(output_lang: str = "pt-BR") -> str:
    raise NotImplementedError("Codex backend not implemented yet")
