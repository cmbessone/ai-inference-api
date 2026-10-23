from __future__ import annotations

from redis.asyncio import Redis

from app.core.cache import (
    RedisCache,
    RedisLock,
    cache_key,
    decode_completion,
    encode_completion,
)
from app.core.clock import ManualClock
from app.core.ports import Completion, Usage
from app.core.redis_gateway import RedisGateway
from tests.fakes import unreachable_redis

CONFIG = {"temperature": 0.7, "max_tokens": 256}


def test_same_request_same_key() -> None:
    """R1-AC09: mismo modelo + input normalizado + config → misma clave."""
    assert cache_key("gpt-4o", "  hola  ", CONFIG) == cache_key("gpt-4o", "hola", CONFIG)


def test_key_is_independent_of_config_order() -> None:
    reordered = {"max_tokens": 256, "temperature": 0.7}
    assert cache_key("gpt-4o", "hola", CONFIG) == cache_key("gpt-4o", "hola", reordered)


def test_model_or_config_change_key() -> None:
    """R1-AC10."""
    base = cache_key("gpt-4o", "hola", CONFIG)
    assert base != cache_key("gpt-4o-mini", "hola", CONFIG)
    assert base != cache_key("gpt-4o", "hola", {**CONFIG, "temperature": 0.2})
    assert base != cache_key("gpt-4o", "Hola", CONFIG)


def test_unicode_normalization() -> None:
    composed, decomposed = "canción", "canción"
    assert cache_key("m", composed, CONFIG) == cache_key("m", decomposed, CONFIG)


def test_completion_roundtrip() -> None:
    completion = Completion("¡hola!", Usage(3, 4))
    assert decode_completion(encode_completion(completion)) == completion


async def test_cache_ttl_is_applied(redis_client: Redis, clock: ManualClock) -> None:
    """R1-AC11: el TTL configurado se aplica a la entrada."""
    cache = RedisCache(RedisGateway(redis_client, clock))
    await cache.set("k", b"v", ttl_s=120)
    assert await cache.get("k") == b"v"
    assert 119_000 < await redis_client.pttl("k") <= 120_000


async def test_cache_degrades_to_miss_when_redis_down(clock: ManualClock) -> None:
    """R1-AC12: Redis caído = cache vacío, nunca error."""
    fallbacks: list[None] = []
    cache = RedisCache(RedisGateway(unreachable_redis(), clock), lambda: fallbacks.append(None))
    await cache.set("k", b"v", ttl_s=10)
    assert await cache.get("k") is None
    assert len(fallbacks) == 2


async def test_lock_has_owner(redis_client: Redis, clock: ManualClock) -> None:
    lock = RedisLock(RedisGateway(redis_client, clock))
    assert await lock.acquire("l", "owner-a", ttl_s=5)
    assert not await lock.acquire("l", "owner-b", ttl_s=5)
    await lock.release("l", "owner-b")  # un no-dueño no puede liberar
    assert not await lock.acquire("l", "owner-b", ttl_s=5)
    await lock.release("l", "owner-a")
    assert await lock.acquire("l", "owner-b", ttl_s=5)
