"""Caso de uso: inferencia sincrónica con cache-aside y protección contra thundering herd.

Escenario A (R1-AC17) — 100 requests idénticos tras un cache miss:

    request ─▶ cache GET ──hit──▶ respuesta
                  │ miss
                  ▼
        SingleFlight local (1 tarea por clave y por réplica)
                  │
                  ▼
        lock distribuido SET NX PX (1 tarea por clave en todo el clúster)
          ├─ líder:     re-chequea cache → LLM → cache SET → libera lock
          └─ seguidor:  espera el valor en cache (polling con backoff)
                        ├─ aparece      → respuesta (coalesced)
                        ├─ líder falló  → mismo error, sin llamar al LLM
                        └─ expira       → llama al LLM él mismo (degradación, nunca bloqueo)
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.core.cache import (
    cache_key,
    decode_completion,
    encode_completion,
    failure_key,
    lock_key,
)
from app.core.clock import Clock
from app.core.errors import DomainError, LLMProviderError, LLMTimeoutError, LLMUnavailableError
from app.core.llm_client import ResilientLLMClient
from app.core.observability.metrics import Metrics
from app.core.ports import Cache, Completion, DistributedLock, LLMRequest
from app.core.single_flight import SingleFlight


class Source(StrEnum):
    LLM = "llm"  # este request generó la llamada al LLM
    CACHE = "cache"  # estaba en cache
    COALESCED = "coalesced"  # otro request idéntico en vuelo hizo la llamada


@dataclass(frozen=True, slots=True)
class InferenceResult:
    completion: Completion
    source: Source

    @property
    def cache_hit(self) -> bool:
        return self.source is not Source.LLM


@dataclass(frozen=True, slots=True)
class CoalescingPolicy:
    cache_ttl_s: float = 3600.0
    lock_ttl_s: float = 35.0
    max_wait_s: float = 30.0
    failure_ttl_s: float = 2.0
    poll_initial_s: float = 0.02
    poll_max_s: float = 0.25


def next_poll_delay(current: float, policy: CoalescingPolicy) -> float:
    return min(current * 1.5, policy.poll_max_s)


def error_from_marker(raw: bytes) -> DomainError:
    """Reconstruye el error que tuvo el líder para que los seguidores fallen igual (pura)."""
    code = raw.decode()
    if code == LLMTimeoutError.code:
        return LLMTimeoutError("El request coalescido excedió el timeout")
    if code == LLMUnavailableError.code:
        return LLMUnavailableError(retry_after_s=1)
    return LLMProviderError("El request coalescido falló", retryable=False, counts_as_failure=False)


class InferenceService:
    def __init__(
        self,
        llm: ResilientLLMClient,
        cache: Cache,
        lock: DistributedLock,
        clock: Clock,
        metrics: Metrics,
        policy: CoalescingPolicy,
    ) -> None:
        self._llm = llm
        self._cache = cache
        self._lock = lock
        self._clock = clock
        self._metrics = metrics
        self._policy = policy
        self._flight: SingleFlight[InferenceResult] = SingleFlight()

    async def infer(self, request: LLMRequest, timeout_s: float) -> InferenceResult:
        key = cache_key(request.model, request.prompt, request.as_cache_config())
        cached = await self._cache.get(key)
        if cached is not None:
            self._metrics.cache_requests.labels("hit").inc()
            return InferenceResult(decode_completion(cached), Source.CACHE)
        self._metrics.cache_requests.labels("miss").inc()

        outcome = await self._flight.do(key, lambda: self._resolve(key, request, timeout_s))
        if outcome.shared:
            self._metrics.coalesced.labels("local").inc()
            return InferenceResult(outcome.value.completion, Source.COALESCED)
        return outcome.value

    async def _resolve(self, key: str, request: LLMRequest, timeout_s: float) -> InferenceResult:
        token = uuid.uuid4().hex
        lock = lock_key(key)
        if not await self._lock.acquire(lock, token, ttl_s=timeout_s + self._policy.lock_ttl_s):
            follower = await self._wait_for_leader(key, timeout_s)
            if follower is not None:
                return follower
            return await self._call_and_store(key, request, timeout_s)  # degradación
        try:
            cached = await self._cache.get(key)  # double-checked: otro líder pudo terminar recién
            if cached is not None:
                return InferenceResult(decode_completion(cached), Source.CACHE)
            return await self._call_and_store(key, request, timeout_s)
        finally:
            await self._lock.release(lock, token)

    async def _call_and_store(
        self, key: str, request: LLMRequest, timeout_s: float
    ) -> InferenceResult:
        try:
            completion = await self._llm.complete(request, timeout_s)
        except (LLMTimeoutError, LLMUnavailableError, LLMProviderError) as exc:
            # Avisar a los seguidores para que no repitan una llamada que acaba de fallar.
            await self._cache.set(failure_key(key), exc.code.encode(), self._policy.failure_ttl_s)
            raise
        await self._cache.set(key, encode_completion(completion), self._policy.cache_ttl_s)
        return InferenceResult(completion, Source.LLM)

    async def _wait_for_leader(self, key: str, timeout_s: float) -> InferenceResult | None:
        deadline = self._clock.monotonic() + min(self._policy.max_wait_s, timeout_s)
        delay = self._policy.poll_initial_s
        while self._clock.monotonic() < deadline:
            await asyncio.sleep(delay)
            cached = await self._cache.get(key)
            if cached is not None:
                self._metrics.coalesced.labels("distributed").inc()
                return InferenceResult(decode_completion(cached), Source.COALESCED)
            marker = await self._cache.get(failure_key(key))
            if marker is not None:
                raise error_from_marker(marker)
            delay = next_poll_delay(delay, self._policy)
        return None
