from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol


class ChatBackend(Protocol):
    """Pluggable chat backend (Claude, Codex/OpenAI, Antigravity, etc.).

    A concrete implementation lives in `app/features/<name>/`. The handler
    layer depends on this Protocol rather than a concrete class so a new
    backend can be added without touching the core flow.
    """

    def start(self) -> None: ...

    def send_and_collect(self, prompt: str, timeout: float | None = None) -> str: ...

    def stream(self, prompt: str, timeout: float | None = None) -> Iterator[str]:
        """Send a turn and yield the response's text deltas as they arrive.

        Lets the handler speak sentence by sentence (realtime) instead of waiting
        for the full response. `send_and_collect` remains available as a fallback.
        """
        ...

    def interrupt(self) -> None: ...

    def stop(self) -> None: ...
