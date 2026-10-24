"""Proveedor LLM simulado (default).

Hace que `docker compose up` funcione sin API keys y permite simular latencia y fallas de forma
controlada para probar el circuit breaker, timeouts y backpressure.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable

from app.core.errors import LLMProviderError
from app.core.ports import Completion, LLMRequest, Usage


def estimate_tokens(text: str) -> int:
    """Aproximación barata (≈ palabras). Suficiente para el mock; OpenAI devuelve el real."""
    return max(1, len(text.split()))


def synthesize_tokens(request: LLMRequest) -> tuple[str, ...]:
    """Respuesta determinista derivada del input (pura): mismo request → mismos tokens."""
    words = request.prompt.split() or ["…"]
    body = ["Respuesta", " simulada", " de", f" {request.model}:"]
    body += [f" {w}" for w in reversed(words)]
    return tuple(body[: request.max_tokens])


class MockProvider:
    name = "mock"

    def __init__(
        self,
        first_token_latency_s: float = 0.05,
        token_latency_s: float = 0.01,
        failure_rate: float = 0.0,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.first_token_latency_s = first_token_latency_s
        self.token_latency_s = token_latency_s
        self.failure_rate = failure_rate
        self._rng = rng
        self.calls = 0
        self.cancelled_streams = 0

    def _maybe_fail(self) -> None:
        if self.failure_rate and self._rng() < self.failure_rate:
            raise LLMProviderError("mock: falla simulada", retryable=True, counts_as_failure=True)

    async def complete(self, request: LLMRequest) -> Completion:
        self.calls += 1
        await asyncio.sleep(self.first_token_latency_s)
        self._maybe_fail()
        tokens = synthesize_tokens(request)
        return Completion(
            text="".join(tokens),
            usage=Usage(tokens_in=estimate_tokens(request.prompt), tokens_out=len(tokens)),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        self.calls += 1
        try:
            await asyncio.sleep(self.first_token_latency_s)
            self._maybe_fail()
            for token in synthesize_tokens(request):
                yield token
                await asyncio.sleep(self.token_latency_s)
        except (asyncio.CancelledError, GeneratorExit):
            self.cancelled_streams += 1  # observable en tests: el upstream se cortó (R2-AC03)
            raise

    async def aclose(self) -> None:
        return None
