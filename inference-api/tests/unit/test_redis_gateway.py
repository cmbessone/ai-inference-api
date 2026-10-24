from __future__ import annotations

import pytest
from redis.asyncio import Redis

from app.core.clock import ManualClock
from app.core.errors import BackendUnavailableError
from app.core.redis_gateway import RedisGateway
from tests.fakes import unreachable_redis


async def test_cooldown_after_repeated_failures(clock: ManualClock) -> None:
    """RS-AC11: tras N fallos deja de intentar Redis y falla al instante."""
    attempts = 0

    async def operation(_: Redis) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("down")

    gateway = RedisGateway(unreachable_redis(), clock, failure_threshold=3, cooldown_s=5)
    for _ in range(5):
        with pytest.raises(BackendUnavailableError):
            await gateway.run(operation)
    assert attempts == 3  # los últimos 2 ni siquiera tocaron la red

    clock.advance(5)
    with pytest.raises(BackendUnavailableError):
        await gateway.run(operation)
    assert attempts == 4  # probe de recuperación


async def test_ping(redis_client: Redis, clock: ManualClock) -> None:
    assert await RedisGateway(redis_client, clock).ping()
    assert not await RedisGateway(unreachable_redis(), clock).ping()
