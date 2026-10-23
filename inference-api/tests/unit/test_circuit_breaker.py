from __future__ import annotations

import pytest
from redis.asyncio import Redis

from app.core.circuit_breaker import (
    BreakerPolicy,
    BreakerSnapshot,
    CircuitBreaker,
    CircuitState,
    FallbackBreakerStore,
    InMemoryBreakerStore,
    RedisBreakerStore,
    admit,
    on_failure,
    on_success,
    state_of,
)
from app.core.clock import ManualClock
from app.core.redis_gateway import RedisGateway
from tests.fakes import unreachable_redis

POLICY = BreakerPolicy(failure_threshold=5, window_s=30, open_s=60, probe_ttl_s=10)


def fail_n(snapshot: BreakerSnapshot, times: int, start: float, step: float) -> BreakerSnapshot:
    for i in range(times):
        snapshot = on_failure(snapshot, start + i * step, POLICY)
    return snapshot


# ── Núcleo puro ─────────────────────────────────────────────────────────────────


def test_opens_after_five_failures_within_window() -> None:
    """RS-AC01: 5 fallos en 30 s → OPEN."""
    snapshot = fail_n(BreakerSnapshot(), 5, start=0, step=5)
    assert state_of(snapshot, 21, POLICY) is CircuitState.OPEN


def test_failures_outside_window_do_not_open() -> None:
    """RS-AC01: la ventana es deslizante; fallos viejos expiran."""
    snapshot = fail_n(BreakerSnapshot(), 5, start=0, step=10)  # 0,10,20,30,40
    assert state_of(snapshot, 40, POLICY) is CircuitState.CLOSED


def test_open_rejects_with_remaining_time() -> None:
    """RS-AC02: en OPEN se rechaza con retry_after = tiempo restante."""
    snapshot = fail_n(BreakerSnapshot(), 5, start=0, step=0)
    admission, _ = admit(snapshot, 15, POLICY)
    assert not admission.allowed
    assert admission.retry_after_s == 45


def test_half_open_allows_single_probe() -> None:
    """RS-AC03: tras 60 s pasa a HALF_OPEN y deja pasar un único probe."""
    snapshot = fail_n(BreakerSnapshot(), 5, start=0, step=0)
    first, snapshot = admit(snapshot, 60, POLICY)
    second, snapshot = admit(snapshot, 60.5, POLICY)
    assert first.allowed
    assert first.is_probe
    assert first.state is CircuitState.HALF_OPEN
    assert not second.allowed


def test_probe_success_closes_and_failure_reopens() -> None:
    """RS-AC04."""
    opened = fail_n(BreakerSnapshot(), 5, start=0, step=0)
    _, probing = admit(opened, 61, POLICY)
    assert state_of(on_success(probing, 62, POLICY), 62, POLICY) is CircuitState.CLOSED
    reopened = on_failure(probing, 62, POLICY)
    assert state_of(reopened, 62, POLICY) is CircuitState.OPEN
    assert state_of(reopened, 62 + 59, POLICY) is CircuitState.OPEN


# ── Stores ──────────────────────────────────────────────────────────────────────


async def drive_to_open(breaker: CircuitBreaker) -> None:
    for _ in range(POLICY.failure_threshold):
        await breaker.record_failure()


async def test_redis_store_is_shared_between_replicas(
    redis_client: Redis, clock: ManualClock
) -> None:
    """RS-AC06: dos réplicas comparten estado; lo que abre una lo ve la otra."""
    gateway = RedisGateway(redis_client, clock)
    replica_a = CircuitBreaker(RedisBreakerStore(gateway, POLICY), clock)
    replica_b = CircuitBreaker(RedisBreakerStore(gateway, POLICY), clock)

    await drive_to_open(replica_a)
    rejected = await replica_b.admit()
    assert not rejected.allowed
    assert rejected.state is CircuitState.OPEN
    assert rejected.retry_after_s == 60


async def test_redis_store_single_probe_across_cluster(
    redis_client: Redis, clock: ManualClock
) -> None:
    """RS-AC03 + RS-AC04 en modo distribuido."""
    gateway = RedisGateway(redis_client, clock)
    replica_a = CircuitBreaker(RedisBreakerStore(gateway, POLICY), clock)
    replica_b = CircuitBreaker(RedisBreakerStore(gateway, POLICY), clock)
    await drive_to_open(replica_a)

    clock.advance(60)
    probe = await replica_a.admit()
    other = await replica_b.admit()
    assert probe.allowed
    assert probe.is_probe
    assert not other.allowed
    assert other.state is CircuitState.HALF_OPEN

    await replica_a.record_success()
    assert (await replica_b.status()).state is CircuitState.CLOSED


async def test_redis_store_probe_failure_reopens(redis_client: Redis, clock: ManualClock) -> None:
    """RS-AC04: si el probe falla, OPEN otros 60 s."""
    breaker = CircuitBreaker(RedisBreakerStore(RedisGateway(redis_client, clock), POLICY), clock)
    await drive_to_open(breaker)
    clock.advance(61)
    assert (await breaker.admit()).is_probe
    await breaker.record_failure()
    status = await breaker.status()
    assert status.state is CircuitState.OPEN
    assert status.retry_after_s == 60


@pytest.mark.parametrize("store_kind", ["memory", "redis"])
async def test_stores_follow_same_rules(
    store_kind: str, redis_client: Redis, clock: ManualClock
) -> None:
    """La implementación Lua y la pura son equivalentes en el ciclo completo."""
    store = (
        InMemoryBreakerStore(POLICY)
        if store_kind == "memory"
        else RedisBreakerStore(RedisGateway(redis_client, clock), POLICY)
    )
    breaker = CircuitBreaker(store, clock)
    for _ in range(4):
        await breaker.record_failure()
        clock.advance(1)
    assert (await breaker.admit()).allowed
    await breaker.record_failure()
    assert not (await breaker.admit()).allowed
    clock.advance(60)
    assert (await breaker.admit()).is_probe
    await breaker.record_success()
    assert (await breaker.status()).state is CircuitState.CLOSED


async def test_fallback_store_uses_memory_when_redis_down(clock: ManualClock) -> None:
    """RS-AC07: sin Redis, cada réplica sigue protegida por un breaker local."""
    store = FallbackBreakerStore(
        RedisBreakerStore(RedisGateway(unreachable_redis(), clock), POLICY),
        InMemoryBreakerStore(POLICY),
    )
    breaker = CircuitBreaker(store, clock)
    await drive_to_open(breaker)
    admission = await breaker.admit()
    assert not admission.allowed
    assert admission.state is CircuitState.OPEN
