from __future__ import annotations

from redis.asyncio import Redis

from app.core.clock import ManualClock
from app.core.rate_limiter import (
    FallbackRateLimiter,
    InMemoryRateLimiter,
    RateLimitPolicy,
    RedisRateLimiter,
    evaluate_window,
)
from app.core.redis_gateway import RedisGateway
from tests.fakes import unreachable_redis

POLICY = RateLimitPolicy(limit=60, window_s=60)


def fill(count: int, start: float = 0.0, step: float = 0.0) -> tuple[float, ...]:
    return tuple(start + i * step for i in range(count))


def test_allows_until_limit_and_reports_remaining() -> None:
    """R1-AC07: remaining decrece con cada request."""
    decision, window = evaluate_window(fill(10), now=1.0, policy=POLICY)
    assert decision.allowed
    assert decision.remaining == 49
    assert len(window) == 11


def test_request_61_is_rejected_with_exact_retry_after() -> None:
    """R1-AC04: el 61 se rechaza; retry_after = cuándo vence el más viejo."""
    hits = fill(60, start=0, step=0.5)  # 0 … 29.5
    decision, window = evaluate_window(hits, now=48.0, policy=POLICY)
    assert not decision.allowed
    assert decision.retry_after_s == 12  # el de t=0 vence en t=60
    assert window == hits  # los rechazados no consumen cuota


def test_window_is_sliding_not_fixed() -> None:
    """R1-AC06: no se permiten 120 requests en el borde de dos minutos."""
    hits = fill(60, start=59.0, step=0.01)  # ráfaga al final del "primer minuto"
    decision, _ = evaluate_window(hits, now=61.0, policy=POLICY)
    assert not decision.allowed


def test_old_hits_expire() -> None:
    decision, window = evaluate_window(fill(60), now=60.0, policy=POLICY)
    assert decision.allowed
    assert window == (60.0,)


async def test_quota_is_shared_between_replicas(redis_client: Redis, clock: ManualClock) -> None:
    """R1-AC05: dos réplicas comparten la cuota vía Redis."""
    gateway = RedisGateway(redis_client, clock)
    replica_a = RedisRateLimiter(gateway, RateLimitPolicy(limit=3, window_s=60), clock)
    replica_b = RedisRateLimiter(gateway, RateLimitPolicy(limit=3, window_s=60), clock)

    results = [
        await replica_a.hit("acme"),
        await replica_b.hit("acme"),
        await replica_a.hit("acme"),
    ]
    assert [r.remaining for r in results] == [2, 1, 0]
    rejected = await replica_b.hit("acme")
    assert not rejected.allowed
    assert rejected.retry_after_s == 60
    assert (await replica_b.hit("other-client")).allowed  # la cuota es por cliente

    clock.advance(60)
    assert (await replica_a.hit("acme")).allowed


async def test_falls_back_to_local_limiter_when_redis_down(clock: ManualClock) -> None:
    """R1-AC08: sin Redis el límite se aplica localmente y se cuenta el fallback."""
    fallbacks: list[None] = []
    policy = RateLimitPolicy(limit=2, window_s=60)
    limiter = FallbackRateLimiter(
        RedisRateLimiter(RedisGateway(unreachable_redis(), clock), policy, clock),
        InMemoryRateLimiter(policy, clock),
        on_fallback=lambda: fallbacks.append(None),
    )
    decisions = [await limiter.hit("acme") for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, False]
    assert len(fallbacks) == 3
