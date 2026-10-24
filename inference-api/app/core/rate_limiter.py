"""Rate limiting por cliente con ventana deslizante exacta (sliding log).

¿Por qué sliding log y no fixed window / token bucket / GCRA? (ADR-005)
- *Fixed window* permite 2x el límite en el borde entre ventanas (viola R1-AC06).
- *Token bucket / GCRA* con ráfaga = 60 permite >60 requests en 60 s consecutivos.
- *Sliding log* es exacto y su costo (un ZSET de ≤ 60 elementos por cliente) es trivial; además
  permite calcular el `retry_after_s` exacto: cuándo vence el request más viejo de la ventana.

La regla vive en `evaluate_window` (pura). El script Lua la replica de forma atómica en Redis
para que todas las réplicas compartan la cuota (R1-AC05) sin race conditions.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.clock import Clock
from app.core.errors import BackendUnavailableError
from app.core.redis_gateway import RedisGateway


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    limit: int = 60
    window_s: float = 60.0


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int = 0


def evaluate_window(
    hits: tuple[float, ...], now: float, policy: RateLimitPolicy
) -> tuple[RateLimitDecision, tuple[float, ...]]:
    """Decide un request y devuelve la nueva ventana. `hits` está ordenado ascendentemente.

    Los requests rechazados no consumen cuota (no se agregan a la ventana).
    """
    live = tuple(t for t in hits if t > now - policy.window_s)
    if len(live) < policy.limit:
        updated = (*live, now)
        return RateLimitDecision(True, policy.limit, policy.limit - len(updated)), updated
    retry = max(1, math.ceil(live[0] + policy.window_s - now))
    return RateLimitDecision(False, policy.limit, 0, retry), live


class RateLimiter(Protocol):
    async def hit(self, key: str) -> RateLimitDecision: ...


class InMemoryRateLimiter:
    """Limitador local por réplica: fallback cuando Redis no está disponible (R1-AC08)."""

    def __init__(self, policy: RateLimitPolicy, clock: Clock) -> None:
        self._policy = policy
        self._clock = clock
        self._windows: dict[str, tuple[float, ...]] = {}

    async def hit(self, key: str) -> RateLimitDecision:
        decision, window = evaluate_window(
            self._windows.get(key, ()), self._clock.now(), self._policy
        )
        self._windows[key] = window
        return decision


_SLIDING_LOG_LUA = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count < limit then
  redis.call('ZADD', KEYS[1], now, ARGV[4])
  redis.call('PEXPIRE', KEYS[1], window)
  return {1, limit - count - 1, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return {0, 0, tonumber(oldest[2]) + window - now}
"""


class RedisRateLimiter:
    def __init__(self, gateway: RedisGateway, policy: RateLimitPolicy, clock: Clock) -> None:
        self._gateway = gateway
        self._policy = policy
        self._clock = clock
        self._script = gateway.register_script(_SLIDING_LOG_LUA)

    async def hit(self, key: str) -> RateLimitDecision:
        now_ms = int(self._clock.now() * 1000)
        member = f"{now_ms}:{uuid.uuid4().hex}"  # miembro único del ZSET
        args: list[int | str] = [now_ms, int(self._policy.window_s * 1000), self._policy.limit]
        args.append(member)
        allowed, remaining, wait_ms = await self._gateway.run(
            lambda _: self._script(keys=[f"ratelimit:{key}"], args=args)
        )
        return RateLimitDecision(
            allowed=bool(allowed),
            limit=self._policy.limit,
            remaining=int(remaining),
            retry_after_s=0 if allowed else max(1, math.ceil(int(wait_ms) / 1000)),
        )


class FallbackRateLimiter:
    """Composite: Redis global; si no responde, límite local por réplica (fail-open controlado)."""

    def __init__(
        self,
        primary: RateLimiter,
        fallback: RateLimiter,
        on_fallback: Callable[[], None] = lambda: None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._on_fallback = on_fallback

    async def hit(self, key: str) -> RateLimitDecision:
        try:
            return await self._primary.hit(key)
        except BackendUnavailableError:
            self._on_fallback()
            return await self._fallback.hit(key)
