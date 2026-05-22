"""Claude chat feature (opt-in extra: `voice-mate[claude]`).

Requires the `claude-agent-sdk` package. Call `is_available()` before
attempting to instantiate `ClaudeRuntime`/`ClaudeChatHandler` to fail
gracefully when the extra is not installed.
"""

from __future__ import annotations

from app.features.claude.chat_handler import ClaudeChatHandler
from app.features.claude.runtime import ClaudeRuntime

__all__ = ["ClaudeChatHandler", "ClaudeRuntime", "is_available"]


def is_available() -> bool:
    """Return True if the `claude-agent-sdk` extra is installed."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True
