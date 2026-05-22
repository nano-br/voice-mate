from __future__ import annotations

from typing import Protocol


class ChatBackend(Protocol):
    """Pluggable chat backend (Claude, Codex/OpenAI, Antigravity, etc.).

    A concrete implementation lives in `app/features/<name>/`. The handler
    layer depends on this Protocol rather than a concrete class so a new
    backend can be added without touching the core flow.
    """

    def start(self) -> None: ...

    def send_and_collect(self, prompt: str, timeout: float | None = None) -> str: ...

    def interrupt(self) -> None: ...

    def stop(self) -> None: ...
