"""Dobles de prueba reutilizables."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from redis.asyncio import Redis

from app.core.errors import LLMProviderError
from app.core.ports import Completion, LLMRequest, Usage
from app.core.redis_gateway import no_retry


class ScriptedProvider:
    """Proveedor controlable: latencia, error forzado y conteo de llamadas."""

    name = "scripted"

    def __init__(
        self,
        *,
        delay_s: float = 0.0,
        error: Exception | None = None,
        tokens: tuple[str, ...] = ("Hola", " mundo"),
        token_delay_s: float = 0.0,
        fail_first: int | None = None,
    ) -> None:
        self.delay_s = delay_s
        self.error = error
        self.tokens = tokens
        self.token_delay_s = token_delay_s
        self.fail_first = fail_first  # si se define, `error` sólo aplica a las primeras N llamadas
        self.calls = 0
        self.stream_closed = asyncio.Event()

    async def complete(self, request: LLMRequest) -> Completion:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        self._maybe_fail()
        return Completion(
            "".join(self.tokens), Usage(len(request.prompt.split()), len(self.tokens))
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        self.calls += 1
        try:
            await asyncio.sleep(self.delay_s)
            self._maybe_fail()
            for token in self.tokens:
                yield token
                await asyncio.sleep(self.token_delay_s)
        finally:
            self.stream_closed.set()

    def _maybe_fail(self) -> None:
        if self.error is None:
            return
        if self.fail_first is None or self.calls <= self.fail_first:
            raise self.error

    async def aclose(self) -> None:
        return None


def provider_down() -> LLMProviderError:
    return LLMProviderError("503", retryable=True, counts_as_failure=True)


def unreachable_redis() -> Redis:
    """Cliente apuntando a un puerto cerrado: simula Redis caído sin esperar timeouts largos."""
    return Redis(
        host="127.0.0.1",
        port=1,
        socket_timeout=0.05,
        socket_connect_timeout=0.05,
        retry=no_retry(),
    )
