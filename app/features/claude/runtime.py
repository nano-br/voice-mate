from __future__ import annotations

import asyncio
import queue
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, cast

# Sentinela de fim de stream colocada na fila pela coroutine produtora.
_STREAM_END = object()


def _model_supports_effort(model: str | None) -> bool:
    """O parâmetro `effort` não é aceito pelo Haiku 4.5 (retorna 400).

    Sonnet/Opus aceitam. Quando o modelo não é conhecido (None → default do SDK),
    assumimos que suporta (o default histórico era Sonnet).
    """
    if not model:
        return True
    return "haiku" not in model.lower()


class ClaudeRuntime:
    """Ponte sync↔asyncio para falar com o claude-agent-sdk.

    Mantém um event loop dedicado em thread daemon e um `ClaudeSDKClient`
    vivo durante toda a vida do app (sessão multi-turn preservada). Métodos
    síncronos `send_and_collect`/`interrupt`/`stop` são chamados de outras
    threads e despacham para o loop via `run_coroutine_threadsafe`.
    """

    def __init__(
        self,
        system_prompt: str | None,
        max_turns: int | None,
        model: str | None = None,
        effort: str | None = None,
        thinking_enabled: bool = True,
    ) -> None:
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._model = model
        self._effort = effort
        self._thinking_enabled = thinking_enabled
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Sobe a thread + loop e abre o ClaudeSDKClient.

        Levanta a exceção original se o bootstrap do client falhar (ex: claude
        CLI não autenticado). Chamadores devem capturar e exibir orientação.
        """
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="ClaudeRuntime")
        self._thread.start()
        future = asyncio.run_coroutine_threadsafe(self._bootstrap(), self._loop)
        try:
            future.result(timeout=60.0)
        except BaseException:
            self._teardown_thread()
            raise

    def send_and_collect(self, prompt: str, timeout: float | None = None) -> str:
        """Envia um turno e retorna a resposta textual completa.

        Bloqueia a thread chamadora até o `receive_response` esgotar (ou ser
        cancelado via `interrupt`). Pode levantar `asyncio.CancelledError` se
        cancelado durante o aguardar.
        """
        if self._loop is None or self._client is None:
            raise RuntimeError("ClaudeRuntime não iniciado")
        future = asyncio.run_coroutine_threadsafe(self._send(prompt), self._loop)
        return future.result(timeout=timeout)

    def stream(self, prompt: str, timeout: float | None = None) -> Iterator[str]:
        """Produz os deltas de texto da resposta conforme chegam (realtime).

        A coroutine produtora roda no loop dedicado e empilha cada `TextBlock.text`
        numa `queue.Queue`; este gerador (rodando na thread chamadora) consome a
        fila. Exceções do lado async são propagadas; `interrupt()` encerra o stream
        naturalmente (o `receive_response` se esgota). Bloquear no `speak()` entre
        os `yield` é o que sobrepõe a fala com a geração que segue enchendo a fila.
        """
        if self._loop is None or self._client is None:
            raise RuntimeError("ClaudeRuntime não iniciado")
        bridge: queue.Queue[object] = queue.Queue()
        future = asyncio.run_coroutine_threadsafe(self._send_stream(prompt, bridge), self._loop)
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while True:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                try:
                    item = bridge.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError(f"Claude excedeu timeout ({timeout:.0f}s) no stream.") from exc
                if item is _STREAM_END:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield cast(str, item)
        finally:
            if not future.done():
                future.cancel()

    def interrupt(self) -> None:
        """Pede ao client para abortar o turno atual (fire-and-forget)."""
        if self._loop is None or self._client is None:
            return
        asyncio.run_coroutine_threadsafe(self._safe_interrupt(), self._loop)

    def stop(self) -> None:
        """Fecha o client e desmonta loop + thread."""
        with self._lock:
            if self._loop is None or self._thread is None:
                return
            loop = self._loop
            thread = self._thread
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            print(f"[ClaudeRuntime] Falha ao fechar client: {exc}", file=sys.stderr)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5.0)
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._loop = None
            self._thread = None
            self._client = None

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _bootstrap(self) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        options_kwargs: dict[str, Any] = {}
        if self._system_prompt is not None:
            options_kwargs["system_prompt"] = self._system_prompt
        if self._max_turns is not None:
            options_kwargs["max_turns"] = self._max_turns
        if self._model is not None:
            options_kwargs["model"] = self._model
        if self._effort is not None and _model_supports_effort(self._model):
            options_kwargs["effort"] = self._effort
        elif self._effort is not None:
            print(
                f"[ClaudeRuntime] effort='{self._effort}' ignorado: {self._model} não suporta o parâmetro.",
                file=sys.stderr,
            )
        if not self._thinking_enabled:
            from claude_agent_sdk import ThinkingConfigDisabled

            options_kwargs["thinking"] = ThinkingConfigDisabled(type="disabled")
        options = ClaudeAgentOptions(**options_kwargs)
        client = ClaudeSDKClient(options=options)
        await client.__aenter__()
        self._client = client

    async def _send(self, prompt: str) -> str:
        from claude_agent_sdk import AssistantMessage, TextBlock

        assert self._client is not None
        await self._client.query(prompt)
        chunks: list[str] = []
        async for msg in self._client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
        return "".join(chunks)

    async def _send_stream(self, prompt: str, bridge: queue.Queue[object]) -> None:
        from claude_agent_sdk import AssistantMessage, TextBlock

        assert self._client is not None
        try:
            await self._client.query(prompt)
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text:
                            bridge.put(block.text)
        except BaseException as exc:  # noqa: BLE001 — propagado ao consumidor via fila
            bridge.put(exc)
        finally:
            bridge.put(_STREAM_END)

    async def _safe_interrupt(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.interrupt()
        except Exception as exc:  # noqa: BLE001
            print(f"[ClaudeRuntime] interrupt falhou: {exc}", file=sys.stderr)

    async def _shutdown(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.__aexit__(None, None, None)
        finally:
            self._client = None

    def _teardown_thread(self) -> None:
        if self._loop is not None and self._thread is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5.0)
            try:
                self._loop.close()
            except Exception:  # noqa: BLE001
                pass
        self._loop = None
        self._thread = None
        self._client = None
