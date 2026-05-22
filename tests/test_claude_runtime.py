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


class FakeClaudeAgentOptions:
    def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401 — kwargs livres do SDK
        self.kwargs = kwargs
        self.system_prompt = kwargs.get("system_prompt")
        self.max_turns = kwargs.get("max_turns")
        self.model = kwargs.get("model")
        self.effort = kwargs.get("effort")
        self.thinking = kwargs.get("thinking")


class FakeThinkingConfigDisabled(dict[str, Any]):
    def __init__(self, type: str = "disabled") -> None:  # noqa: A002
        super().__init__()
        self["type"] = type


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    _fake_state.clear()
    module = types.ModuleType("claude_agent_sdk")
    module.ClaudeSDKClient = FakeClaudeSDKClient  # type: ignore[attr-defined]
    module.ClaudeAgentOptions = FakeClaudeAgentOptions  # type: ignore[attr-defined]
    module.AssistantMessage = FakeAssistantMessage  # type: ignore[attr-defined]
    module.TextBlock = FakeTextBlock  # type: ignore[attr-defined]
    module.ThinkingConfigDisabled = FakeThinkingConfigDisabled  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    return module


def test_runtime_start_and_send(fake_sdk: types.ModuleType) -> None:
    from app.features.claude.runtime import ClaudeRuntime

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
    from app.features.claude.runtime import ClaudeRuntime

    _fake_state["aenter_should_raise"] = True
    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    with pytest.raises(RuntimeError, match="claude CLI"):
        runtime.start()


def test_runtime_interrupt_is_fire_and_forget(fake_sdk: types.ModuleType) -> None:
    from app.features.claude.runtime import ClaudeRuntime

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
    from app.features.claude.runtime import ClaudeRuntime

    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    with pytest.raises(RuntimeError, match="não iniciado"):
        runtime.send_and_collect("x")


def test_runtime_stop_is_idempotent(fake_sdk: types.ModuleType) -> None:
    from app.features.claude.runtime import ClaudeRuntime

    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    runtime.start()
    runtime.stop()
    runtime.stop()  # não deve levantar


def test_runtime_passes_model_effort_and_disables_thinking_by_default(
    fake_sdk: types.ModuleType,
) -> None:
    from app.features.claude.runtime import ClaudeRuntime

    runtime = ClaudeRuntime(
        system_prompt="custom",
        max_turns=5,
        model="claude-sonnet-4-6",
        effort="low",
        thinking_enabled=False,
    )
    runtime.start()
    try:
        client = _fake_state["last_client"]
        opts = client.options
        assert opts.model == "claude-sonnet-4-6"
        assert opts.effort == "low"
        assert opts.system_prompt == "custom"
        assert opts.max_turns == 5
        assert opts.thinking == {"type": "disabled"}
    finally:
        runtime.stop()


def test_runtime_thinking_enabled_omits_thinking_kwarg(fake_sdk: types.ModuleType) -> None:
    from app.features.claude.runtime import ClaudeRuntime

    runtime = ClaudeRuntime(
        system_prompt=None,
        max_turns=None,
        model="claude-sonnet-4-6",
        effort="medium",
        thinking_enabled=True,
    )
    runtime.start()
    try:
        opts = _fake_state["last_client"].options
        assert opts.thinking is None  # campo não foi adicionado a kwargs
        assert "thinking" not in opts.kwargs
    finally:
        runtime.stop()


def test_runtime_omits_optional_fields_when_none(fake_sdk: types.ModuleType) -> None:
    from app.features.claude.runtime import ClaudeRuntime

    runtime = ClaudeRuntime(system_prompt=None, max_turns=None)
    runtime.start()
    try:
        opts = _fake_state["last_client"].options
        # nenhum dos optional fields foi setado
        assert "system_prompt" not in opts.kwargs
        assert "max_turns" not in opts.kwargs
        assert "model" not in opts.kwargs
        assert "effort" not in opts.kwargs
    finally:
        runtime.stop()


def test_claude_chat_config_default_leaves_system_prompt_none() -> None:
    """Default system_prompt is None — main.py resolves it to canonical via output_lang."""
    from app.core.config import ClaudeChatConfig

    cfg = ClaudeChatConfig()
    assert cfg.system_prompt is None
    assert cfg.model == "claude-sonnet-4-6"
    assert cfg.effort == "low"
    assert cfg.thinking_enabled is False
    assert cfg.timeout_seconds == 120.0
