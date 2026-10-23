"""Acceso a Redis con *fail fast* (RS-AC11).

Redis es una dependencia crítica para la consistencia (rate limit, breaker, cache) pero **no**
para la disponibilidad: si se cae, la API degrada. Para que la caída no se convierta en latencia
(cada request esperando un timeout), todas las operaciones pasan por este gateway que:

1. Aplica un timeout de socket corto.
2. Cuenta fallos en un circuit breaker **local** (reutiliza `InMemoryBreakerStore`): tras N fallos
   deja de intentar Redis durante un cooldown y falla inmediatamente con `BackendUnavailableError`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.commands.core import AsyncScript
from redis.exceptions import RedisError

from app.core.circuit_breaker import BreakerPolicy, CircuitBreaker, InMemoryBreakerStore
from app.core.clock import Clock
from app.core.errors import BackendUnavailableError

T = TypeVar("T")


def no_retry() -> Retry:
    """redis-py reintenta 3 veces con backoff por defecto: con Redis caído eso son segundos por
    operación. La resiliencia la maneja este gateway (fail fast + cooldown), no el cliente.
    """
    return Retry(NoBackoff(), retries=0)


class RedisGateway:
    def __init__(
        self,
        client: Redis,
        clock: Clock,
        failure_threshold: int = 3,
        cooldown_s: float = 5.0,
    ) -> None:
        self._client = client
        policy = BreakerPolicy(
            failure_threshold=failure_threshold,
            window_s=cooldown_s * 2,
            open_s=cooldown_s,
            probe_ttl_s=cooldown_s,
        )
        self._guard = CircuitBreaker(InMemoryBreakerStore(policy), clock)

    @classmethod
    def from_url(
        cls,
        url: str,
        clock: Clock,
        *,
        socket_timeout_s: float,
        max_connections: int,
        failure_threshold: int,
        cooldown_s: float,
    ) -> RedisGateway:
        client = Redis.from_url(
            url,
            socket_timeout=socket_timeout_s,
            socket_connect_timeout=socket_timeout_s,
            max_connections=max_connections,
            health_check_interval=30,
            retry_on_timeout=False,
            retry=no_retry(),
        )
        return cls(client, clock, failure_threshold, cooldown_s)

    @property
    def client(self) -> Redis:
        return self._client

    def register_script(self, source: str) -> AsyncScript:
        return self._client.register_script(source)

    async def run(self, operation: Callable[[Redis], Awaitable[T]]) -> T:
        admission = await self._guard.admit()
        if not admission.allowed:
            raise BackendUnavailableError("redis en cooldown")
        try:
            result = await operation(self._client)
        except (RedisError, OSError, TimeoutError) as exc:
            await self._guard.record_failure()
            raise BackendUnavailableError(str(exc)) from exc
        await self._guard.record_success()
        return result

    async def ping(self) -> bool:
        try:
            pong: Any = await self.run(lambda r: r.ping())
        except BackendUnavailableError:
            return False
        return bool(pong)

    async def aclose(self) -> None:
        await self._client.aclose()
