"""Caso de uso: streaming SSE con backpressure, cancelación y auditoría (R2, Escenario C).

    proveedor LLM ──▶ [tarea productora] ──▶ Queue(maxsize=N) ──▶ [generador consumidor] ──▶ socket
                         stream_into()          buffer acotado       StreamingResponse

- **Backpressure** (R2-AC07): si el cliente lee lento, uvicorn deja de drenar el generador
  (control de flujo del transporte), la cola se llena, `queue.put` bloquea a la productora y ésta
  deja de leer del proveedor: la presión llega hasta el socket TCP upstream. Memoria acotada a
  N tokens por stream, sin importar cuán lento sea el cliente.
- **Slow consumer** (R2-AC08): si la cola sigue llena más de `slow_consumer_timeout_s`, se
  aborta: se cancela el upstream, se reemplaza el buffer por un evento de error y se libera todo.
- **Desconexión** (R2-AC03): Starlette cancela el generador; su `finally` cancela la productora
  y ésta cierra el iterador del proveedor, que cierra la conexión HTTP upstream.
- **Auditoría** (R2-AC05): la productora, en su `finally`, encola el registro sin bloquear.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.core.audit import AuditRecord, AuditSink
from app.core.clock import Clock
from app.core.errors import DomainError, LLMTimeoutError, LLMUnavailableError, SlowConsumerError
from app.core.llm_client import ResilientLLMClient
from app.core.observability.context import current_context
from app.core.observability.metrics import Metrics
from app.core.ports import LLMRequest
from app.core.sse import DONE, error_event, token_event

CLIENT_DISCONNECT = "client_disconnect"


@dataclass(frozen=True, slots=True)
class StreamingPolicy:
    buffer_size: int = 64
    slow_consumer_timeout_s: float = 10.0


@dataclass(frozen=True, slots=True)
class StreamIdentity:
    request_id: str
    client_id: str


@dataclass(slots=True)
class _StreamState:
    """Estado mutable de *un* stream (vive sólo durante el request)."""

    tokens: list[str] = field(default_factory=list)
    end_reason: str = CLIENT_DISCONNECT
    finished: bool = False


def terminal_event(error: DomainError) -> bytes:
    """Evento final para cada error de dominio (pura)."""
    if isinstance(error, LLMUnavailableError):
        return error_event(error.code, error.retry_after_s)
    return error_event(error.code)


class StreamingService:
    def __init__(
        self,
        llm: ResilientLLMClient,
        audit: AuditSink,
        clock: Clock,
        metrics: Metrics,
        policy: StreamingPolicy,
    ) -> None:
        self._llm = llm
        self._audit = audit
        self._clock = clock
        self._metrics = metrics
        self._policy = policy

    async def events(
        self, request: LLMRequest, timeout_s: float, identity: StreamIdentity
    ) -> AsyncIterator[bytes]:
        queue: asyncio.Queue[tuple[bytes, bool]] = asyncio.Queue(self._policy.buffer_size)
        state = _StreamState()
        producer = asyncio.create_task(
            self._produce(request, timeout_s, identity, queue, state), name="sse-producer"
        )
        try:
            while True:
                event, is_terminal = await queue.get()
                yield event
                if is_terminal:
                    state.finished = True
                    return
        finally:
            if not producer.done() and not state.finished:
                producer.cancel()  # desconexión: corta el upstream
                self._metrics.stream_cancelled.labels(CLIENT_DISCONNECT).inc()

    async def _produce(
        self,
        request: LLMRequest,
        timeout_s: float,
        identity: StreamIdentity,
        queue: asyncio.Queue[tuple[bytes, bool]],
        state: _StreamState,
    ) -> None:
        started = self._clock.monotonic()

        async def emit(token: str) -> None:
            event = token_event(token, len(state.tokens))
            state.tokens.append(token)
            try:
                async with asyncio.timeout(self._policy.slow_consumer_timeout_s):
                    await queue.put((event, False))
            except TimeoutError as exc:
                raise SlowConsumerError("El cliente no consume el stream a tiempo") from exc

        try:
            try:
                await self._llm.stream_into(request, timeout_s, emit)
            except SlowConsumerError as exc:
                _drain(queue)  # el cliente es lento: descartar lo pendiente y avisar
                self._finish(queue, state, terminal_event(exc), exc.code)
            except DomainError as exc:
                self._finish(queue, state, terminal_event(exc), exc.code)
            else:
                self._finish(queue, state, DONE, "completed")
        finally:
            self._submit_audit(request, identity, state, started)

    def _finish(
        self,
        queue: asyncio.Queue[tuple[bytes, bool]],
        state: _StreamState,
        event: bytes,
        reason: str,
    ) -> None:
        state.end_reason = reason
        if reason in {LLMTimeoutError.code, SlowConsumerError.code}:
            self._metrics.stream_cancelled.labels(reason).inc()
        if (ctx := current_context()) is not None and reason != "completed":
            ctx.outcome = "timeout" if reason == LLMTimeoutError.code else "error"
        if queue.full():
            _drain(queue)
        queue.put_nowait((event, True))

    def _submit_audit(
        self, request: LLMRequest, identity: StreamIdentity, state: _StreamState, started: float
    ) -> None:
        self._audit.submit(
            AuditRecord(
                request_id=identity.request_id,
                client_id=identity.client_id,
                model=request.model,
                prompt=request.prompt,
                output="".join(state.tokens),
                tokens_out=len(state.tokens),
                duration_ms=round((self._clock.monotonic() - started) * 1000),
                end_reason=state.end_reason,
                timestamp=self._clock.now(),
            )
        )


def _drain(queue: asyncio.Queue[tuple[bytes, bool]]) -> None:
    while not queue.empty():
        queue.get_nowait()
