"""Hub de eventos da sessão: estado vivo + último resultado, por consumidor.

No WSL2 o gatilho vem de fora (o script de hotkeys do Windows faz HTTP no
daemon). O protocolo antigo era *fire-and-forget* e *toggle*: o `/trigger`
respondia sem dizer o que fez, e qualquer evento perdido/duplicado dessincroniza
os dois lados em silêncio (o usuário aperta querendo gravar e o WSL para). Este
hub conserta isso na raiz — ele carrega ESTADO:

  - registro de consumidores (`register()` → `client_id` gerado no WSL);
  - a operação CORRENTE (idle/recording/processing) com o `op_seq` e quem a
    iniciou;
  - o ÚLTIMO resultado (texto + `seq` crescente) correlacionado à operação.

Duas modalidades de consulta, ambas disponíveis desde já:
  - `scope="all"`  — o estado/resultado GLOBAL (default do Windows hoje: todos os
    consumidores ouvem tudo). O `seq`/`op_seq` é monotônico, então o consumidor
    sabe se o que recebeu é MAIS NOVO que o último que guardou.
  - `scope="mine"` — só o que ESTE `client_id` iniciou (o Ctrl+C "só meu").

Hoje há uma única fonte de áudio (um microfone), então a operação corrente é
global; o `client_id` apenas etiqueta quem a iniciou. A rota individual já existe
como fundação para, no futuro, múltiplos consumidores gravarem em paralelo.

Alimentado por dois lados: a `RecordingSession` reporta as transições de estado;
o `ClipboardWriter` publica o texto final (`record_result`).

Os resultados ficam num BUFFER circular (não só o último): o produtor (WSL) pode
gerar mais rápido do que o consumidor (Windows) faz polling — em testes em
sequência, dois resultados terminam perto um do outro e, com slot único, o
primeiro era sobrescrito antes de ser lido (perdia ~metade). Com o buffer, o
consumidor pede `result(..., since=<seq>)` e DRENA em ordem o próximo não-visto,
sem perder os intermediários.
"""

from __future__ import annotations

import secrets
import threading
from collections import deque
from dataclasses import dataclass, replace
from typing import Literal

# Quantos resultados recentes manter por buffer (global e por cliente). Folgado:
# o consumidor polla a cada 150-300ms, então só "atrasa" alguns; 64 cobre rajadas
# bem maiores que qualquer teste em sequência realista.
_RESULT_BUFFER = 64

SessionState = Literal["idle", "recording", "processing"]
Scope = Literal["all", "mine"]
ToggleAction = Literal["started", "stopped", "restarted"]


@dataclass(frozen=True)
class ToggleOutcome:
    """O que um `toggle()` fez — devolvido na hora ao gatilho (sem esperar STT).

    `started` = idle→recording · `stopped` = recording→processing (transcrevendo)
    · `restarted` = processing→recording (cancelou e recomeçou).
    """

    action: ToggleAction
    op_seq: int
    state: SessionState
    flow: str


@dataclass(frozen=True)
class Operation:
    """Snapshot da operação corrente (ou da última de um consumidor)."""

    op_seq: int
    state: SessionState
    flow: str | None
    client_id: str | None


@dataclass(frozen=True)
class Result:
    """Último texto produzido, correlacionado à operação que o gerou."""

    seq: int
    text: str
    op_seq: int
    client_id: str | None


_IDLE = Operation(op_seq=0, state="idle", flow=None, client_id=None)
_EMPTY = Result(seq=0, text="", op_seq=0, client_id=None)


class SessionStatus:
    """Estado vivo + último resultado da sessão, consultável por consumidor.

    Thread-safe. A `RecordingSession` é a autoridade do `op_seq` (passa nas
    chamadas `set_operation`); o store apenas guarda snapshots e correlaciona o
    resultado com a operação corrente.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: set[str] = set()
        self._result_seq = 0
        self._current = _IDLE
        self._last_result = _EMPTY
        # Buffer circular dos resultados recentes (global), para o consumidor
        # drenar em ordem via `result(since=...)` sem perder os intermediários.
        self._results: deque[Result] = deque(maxlen=_RESULT_BUFFER)
        # Por consumidor: a última operação que ELE iniciou e o buffer dos
        # resultados dele (modalidade scope="mine").
        self._client_op: dict[str, Operation] = {}
        self._client_result: dict[str, Result] = {}
        self._client_results: dict[str, deque[Result]] = {}

    # -- registro de consumidores ------------------------------------------
    def register(self) -> str:
        """Gera e registra um `client_id` novo (uuid curto, gerado no WSL)."""
        client_id = secrets.token_hex(8)
        with self._lock:
            self._clients.add(client_id)
        return client_id

    def is_registered(self, client_id: str) -> bool:
        with self._lock:
            return client_id in self._clients

    # -- transições de estado (chamadas pela RecordingSession) -------------
    def set_operation(self, op_seq: int, state: SessionState, flow: str | None, client_id: str | None) -> None:
        """Atualiza a operação corrente. `op_seq` novo (start) ou o mesmo (stop)."""
        op = Operation(op_seq=op_seq, state=state, flow=flow, client_id=client_id)
        with self._lock:
            self._current = op
            if client_id is not None:
                self._client_op[client_id] = op

    def mark_idle(self, op_seq: int) -> None:
        """Volta a corrente a idle se ainda for a operação `op_seq` (sem corrida)."""
        with self._lock:
            if self._current.op_seq == op_seq:
                self._current = replace(self._current, state="idle")
                cid = self._current.client_id
                if cid is not None and cid in self._client_op:
                    self._client_op[cid] = replace(self._client_op[cid], state="idle")

    # -- resultado (chamado pelo ClipboardWriter ao copiar) ----------------
    def record_result(self, text: str) -> None:
        """Publica o texto final, correlacionando-o à operação CORRENTE."""
        with self._lock:
            self._result_seq += 1
            op = self._current
            result = Result(seq=self._result_seq, text=text, op_seq=op.op_seq, client_id=op.client_id)
            self._last_result = result
            self._results.append(result)
            if op.client_id is not None:
                self._client_result[op.client_id] = result
                self._client_results.setdefault(op.client_id, deque(maxlen=_RESULT_BUFFER)).append(result)

    # -- consultas ---------------------------------------------------------
    def status(self, client_id: str | None, scope: Scope) -> dict[str, object]:
        with self._lock:
            op = self._client_op.get(client_id, _IDLE) if scope == "mine" and client_id else self._current
            return {
                "state": op.state,
                "op_seq": op.op_seq,
                "flow": op.flow,
                "client_id": op.client_id,
                "is_yours": client_id is not None and op.client_id == client_id,
                "result_seq": self._last_result.seq,
                "scope": scope,
            }

    def result(self, client_id: str | None, scope: Scope, since: int | None = None) -> dict[str, object]:
        """Último resultado — ou, com `since`, o PRÓXIMO não-visto (seq > since).

        O consumidor drena em ordem: pede `since=<último seq que tratei>` e recebe
        o resultado de menor seq ainda não visto. Quando não há mais nada novo,
        devolve o último (cujo seq é <= since), então o consumidor para de drenar.
        """
        with self._lock:
            if scope == "mine" and client_id:
                latest = self._client_result.get(client_id, _EMPTY)
                buffer: deque[Result] | None = self._client_results.get(client_id)
            else:
                latest = self._last_result
                buffer = self._results
            res = latest
            if since is not None and buffer:
                res = min((r for r in buffer if r.seq > since), key=lambda r: r.seq, default=latest)
            return {
                "seq": res.seq,
                "text": res.text,
                "op_seq": res.op_seq,
                "client_id": res.client_id,
                "scope": scope,
            }

    # -- compat: a API antiga (seq, text) global ---------------------------
    def get(self) -> tuple[int, str]:
        with self._lock:
            return self._last_result.seq, self._last_result.text
