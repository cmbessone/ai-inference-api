"""Cliente LLM resiliente (patrón Decorator sobre `LLMProvider`).

Envuelve cualquier proveedor y agrega, en este orden:

1. **Circuit breaker** — si está abierto, falla en microsegundos sin tocar la red (RS-AC02).
2. **Bulkhead** — semáforo que acota las llamadas concurrentes al LLM por réplica (RS-AC09):
   un proveedor lento no puede acaparar todas las conexiones ni la memoria.
3. **Timeout** — presupuesto total por request (R1-AC16, R2-AC04).
4. **Retry** — sólo errores transitorios, backoff exponencial con *full jitter*, acotado por el
   tiempo restante y nunca con el circuito abierto (RS-AC12).

Las rutas no saben nada de esto: piden una completion y reciben una o un error de dominio.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from app.core.circuit_breaker import CircuitBreaker
from app.core.clock import Clock
from app.core.errors import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
    OverloadedError,
)
from app.core.observability.metrics import Metrics
from app.core.ports import Completion, LLMProvider, LLMRequest

TokenSink = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 1
    base_delay_s: float = 0.1
    max_delay_s: float = 2.0


def backoff_delay(attempt: int, policy: RetryPolicy, rand: float) -> float:
    """Full jitter (AWS Architecture Blog): uniforme en [0, min(cap, base·2^attempt)]."""
    return rand * min(policy.max_delay_s, policy.base_delay_s * (2.0**attempt))


class ResilientLLMClient:
    def __init__(
        self,
        provider: LLMProvider,
        breaker: CircuitBreaker,
        clock: Clock,
        metrics: Metrics,
        *,
        max_concurrency: int,
        queue_timeout_s: float,
        retry: RetryPolicy,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._provider = provider
        self._breaker = breaker
        self._clock = clock
        self._metrics = metrics
        self._bulkhead = asyncio.Semaphore(max_concurrency)
        self._queue_timeout_s = queue_timeout_s
        self._retry = retry
        self._rng = rng

    @property
    def provider_name(self) -> str:
        return self._provider.name

    async def complete(self, request: LLMRequest, timeout_s: float) -> Completion:
        deadline = self._clock.monotonic() + timeout_s
        attempt = 0
        while True:
            await self._admit()
            try:
                completion = await self._call_once(request, deadline)
            except LLMProviderError as exc:
                delay = backoff_delay(attempt, self._retry, self._rng())
                remaining = deadline - self._clock.monotonic()
                if exc.retryable and attempt < self._retry.max_retries and delay < remaining:
                    attempt += 1
                    await asyncio.sleep(delay)
                    continue
                raise
            self._metrics.llm_tokens.labels("in").inc(completion.usage.tokens_in)
            self._metrics.llm_tokens.labels("out").inc(completion.usage.tokens_out)
            return completion

    async def stream_into(self, request: LLMRequest, timeout_s: float, emit: TokenSink) -> int:
        """Empuja tokens a `emit` hasta terminar. Devuelve la cantidad de tokens emitidos.

        Se usa *push* (callback) en vez de devolver un async generator para que el timeout
        envuelva una corrutina normal: `asyncio.timeout` dentro de un generator que hace `yield`
        es frágil. Sólo los timeouts esperando al proveedor cuentan como fallo del breaker; si
        el que tarda es el cliente (slow consumer) el proveedor no tiene la culpa.
        """
        await self._admit()
        emitted = 0
        waiting_upstream = True
        async with self._bulkhead_slot():
            iterator = aiter(self._provider.stream(request))
            try:
                async with asyncio.timeout(timeout_s):
                    while True:
                        waiting_upstream = True
                        try:
                            token = await anext(iterator)
                        except StopAsyncIteration:
                            break
                        waiting_upstream = False
                        await emit(token)
                        emitted += 1
            except TimeoutError as exc:
                if waiting_upstream:
                    await self._on_failure("timeout")
                raise LLMTimeoutError("El stream excedió el timeout") from exc
            except LLMProviderError as exc:
                if exc.counts_as_failure:
                    await self._on_failure("error")
                raise
            finally:
                await _aclose(iterator)  # corta la conexión upstream si no terminó
        await self._on_success()
        self._metrics.llm_tokens.labels("out").inc(emitted)
        return emitted

    async def _admit(self) -> None:
        admission = await self._breaker.admit()
        self._metrics.set_circuit_state(admission.state.value)
        if not admission.allowed:
            self._metrics.llm_calls.labels(self.provider_name, "rejected_open").inc()
            raise LLMUnavailableError(retry_after_s=admission.retry_after_s)

    async def _call_once(self, request: LLMRequest, deadline: float) -> Completion:
        async with self._bulkhead_slot():
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                raise LLMTimeoutError("Sin tiempo restante para llamar al LLM")
            try:
                async with asyncio.timeout(remaining):
                    completion = await self._provider.complete(request)
            except TimeoutError as exc:
                await self._on_failure("timeout")
                raise LLMTimeoutError(f"El LLM no respondió en {remaining:.1f}s") from exc
            except LLMProviderError as exc:
                if exc.counts_as_failure:
                    await self._on_failure("error")
                raise
        await self._on_success()
        return completion

    @asynccontextmanager
    async def _bulkhead_slot(self) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout(self._queue_timeout_s):
                await self._bulkhead.acquire()
        except TimeoutError as exc:
            raise OverloadedError("Capacidad de llamadas al LLM agotada") from exc
        try:
            yield
        finally:
            self._bulkhead.release()

    async def _on_failure(self, outcome: str) -> None:
        self._metrics.llm_calls.labels(self.provider_name, outcome).inc()
        await self._breaker.record_failure()

    async def _on_success(self) -> None:
        self._metrics.llm_calls.labels(self.provider_name, "success").inc()
        await self._breaker.record_success()

    async def circuit_state(self) -> str:
        status = await self._breaker.status()
        self._metrics.set_circuit_state(status.state.value)
        return status.state.value

    async def aclose(self) -> None:
        await self._provider.aclose()


async def _aclose(iterator: AsyncIterator[str]) -> None:
    close = getattr(iterator, "aclose", None)
    if close is not None:
        await close()
