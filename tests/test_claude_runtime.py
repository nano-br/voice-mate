from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

# Variáveis globais do fake — manipuladas pelos testes
_fake_state: dict[str, Any] = {}


@dataclass
class FakeTextBlock:
    text: str


@dataclass
class FakeAssistantMessage:
    content: list[Any]


class FakeClaudeSDKClient:
    def __init__(self, options: FakeClaudeAgentOptions) -> None:
        self.options = options
        self.queries: list[str] = []
        self.interrupt_calls = 0
        self.entered = False
        self.exited = False
        _fake_state["last_client"] = self

    async def __aenter__(self) -> FakeClaudeSDKClient:
        self.entered = True
        if _fake_state.get("aenter_should_raise"):
            raise RuntimeError("claude CLI não autenticado")
        return self

    async def __aexit__(self, *args: object) -> None:
        self.exited = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self) -> AsyncIterator[FakeAssistantMessage]:
        chunks = _fake_state.get("response_chunks", ["resposta da IA"])
        delay = _fake_state.get("response_delay", 0.0)
        for chunk in chunks:
            if delay:
                await asyncio.sleep(delay)
            yield FakeAssistantMessage(content=[FakeTextBlock(text=chunk)])

    async def interrupt(self) -> None:
        self.interrupt_calls += 1


@dataclass
class FakeClaudeAgentOptions:
    system_prompt: str | None = None
    max_turns: int | None = None


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _fake_state.clear()
    module = types.ModuleType("claude_agent_sdk")
    module.ClaudeSDKClient = FakeClaudeSDKClient  # type: ignore[attr-defined]
    module.ClaudeAgentOptions = FakeClaudeAgentOptions  # type: ignore[attr-defined]
    module.AssistantMessage = FakeAssistantMessage  # type: ignore[attr-defined]
    module.TextBlock = FakeTextBlock  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    return module


def test_runtime_start_and_send(fake_sdk: types.ModuleType) -> None:
    from app.services.claude_runtime import ClaudeRuntime

    _fake_state["response_chunks"] = ["olá ", "mundo"]
    runtime = ClaudeRuntime(system_prompt="sys", max_turns=10)
    runtime.start()
    try:
        response = runtime.send_and_collect("pergunta")
        assert response == "olá mundo"
        client = _fake_state["last_client"]
        assert client.queries == ["pergunta"]
        assert client.options.system_prompt == "sys"
        assert client.options.max_turns == 10
    finally:
        runtime.stop()
    assert _fake_state["last_client"].exited


def test_runtime_start_raises_when_aenter_fails(fake_sdk: types.ModuleType) -> None:
    from app.services.claude_runtime import ClaudeRuntime

    _fake_state["aenter_should_raise"] = True
    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    with pytest.raises(RuntimeError, match="claude CLI"):
        runtime.start()


def test_runtime_interrupt_is_fire_and_forget(fake_sdk: types.ModuleType) -> None:
    from app.services.claude_runtime import ClaudeRuntime

    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    runtime.start()
    try:
        runtime.send_and_collect("oi")
        runtime.interrupt()
        # interrupt é async — damos uma janela curta para o loop processar
        import time

        deadline = time.monotonic() + 1.0
        client = _fake_state["last_client"]
        while client.interrupt_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert client.interrupt_calls == 1
    finally:
        runtime.stop()


def test_runtime_send_before_start_raises(fake_sdk: types.ModuleType) -> None:
    from app.services.claude_runtime import ClaudeRuntime

    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    with pytest.raises(RuntimeError, match="não iniciado"):
        runtime.send_and_collect("x")


def test_runtime_stop_is_idempotent(fake_sdk: types.ModuleType) -> None:
    from app.services.claude_runtime import ClaudeRuntime

    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    runtime.start()
    runtime.stop()
    runtime.stop()  # não deve levantar
