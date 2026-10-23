"""Escenario A — thundering herd (R1-AC17)."""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest
from redis.asyncio import Redis

from app.core.cache import RedisCache, RedisLock
from app.core.circuit_breaker import BreakerPolicy, CircuitBreaker, InMemoryBreakerStore
from app.core.clock import SystemClock
from app.core.errors import LLMProviderError
from app.core.llm_client import ResilientLLMClient, RetryPolicy
from app.core.observability.metrics import Metrics
from app.core.ports import LLMRequest
from app.core.redis_gateway import RedisGateway
from app.core.single_flight import SingleFlight
from app.services.inference import CoalescingPolicy, InferenceService, Source
from tests.fakes import ScriptedProvider, provider_down

REQUEST = LLMRequest(model="gpt-4o", prompt="¿qué es un thundering herd?")


def make_service(provider: ScriptedProvider, redis_client: Redis) -> InferenceService:
    clock = SystemClock()
    gateway = RedisGateway(redis_client, clock)
    metrics = Metrics()
    llm = ResilientLLMClient(
        provider,
        CircuitBreaker(InMemoryBreakerStore(BreakerPolicy()), clock),
        clock,
        metrics,
        max_concurrency=100,
        queue_timeout_s=1,
        retry=RetryPolicy(max_retries=0),
    )
    return InferenceService(
        llm,
        RedisCache(gateway),
        RedisLock(gateway),
        clock,
        metrics,
        CoalescingPolicy(poll_initial_s=0.005, poll_max_s=0.01),
    )


async def test_single_flight_shares_one_execution() -> None:
    calls = 0

    async def work() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return 42

    flight: SingleFlight[int] = SingleFlight()
    results = await asyncio.gather(*(flight.do("k", work) for _ in range(20)))
    assert calls == 1
    assert {r.value for r in results} == {42}
    assert sum(not r.shared for r in results) == 1
    assert flight.inflight() == 0


async def test_single_flight_survives_leader_cancellation() -> None:
    """Si el primer llamador se desconecta, los demás reciben el resultado igual."""

    async def work() -> str:
        await asyncio.sleep(0.02)
        return "ok"

    flight: SingleFlight[str] = SingleFlight()
    leader = asyncio.create_task(flight.do("k", work))
    await asyncio.sleep(0)
    follower = asyncio.create_task(flight.do("k", work))
    await asyncio.sleep(0)
    leader.cancel()
    assert (await follower).value == "ok"


async def test_100_identical_requests_one_llm_call_in_one_replica(redis_client: Redis) -> None:
    """R1-AC17 (local): 100 requests concurrentes → 1 llamada al LLM."""
    provider = ScriptedProvider(delay_s=0.05)
    service = make_service(provider, redis_client)

    results = await asyncio.gather(*(service.infer(REQUEST, timeout_s=5) for _ in range(100)))

    assert provider.calls == 1
    sources = Counter(r.source for r in results)
    assert sources[Source.LLM] == 1
    assert sources[Source.COALESCED] == 99


async def test_identical_requests_across_replicas_one_llm_call(redis_client: Redis) -> None:
    """R1-AC17 (distribuido): 3 réplicas x 34 requests → 1 llamada al LLM en el clúster."""
    provider = ScriptedProvider(delay_s=0.05)
    replicas = [make_service(provider, redis_client) for _ in range(3)]

    results = await asyncio.gather(
        *(replica.infer(REQUEST, timeout_s=5) for replica in replicas for _ in range(34))
    )

    assert provider.calls == 1
    assert len({r.completion.text for r in results}) == 1


async def test_followers_fail_fast_when_leader_fails(redis_client: Redis) -> None:
    """Si el líder falla, los seguidores de otras réplicas no repiten la llamada."""
    provider = ScriptedProvider(delay_s=0.05, error=provider_down())
    replicas = [make_service(provider, redis_client) for _ in range(2)]

    outcomes = await asyncio.gather(
        *(replica.infer(REQUEST, timeout_s=5) for replica in replicas for _ in range(10)),
        return_exceptions=True,
    )
    assert all(isinstance(o, LLMProviderError) for o in outcomes)
    assert provider.calls == 1


async def test_second_request_is_served_from_cache(redis_client: Redis) -> None:
    """R1-AC09."""
    provider = ScriptedProvider()
    service = make_service(provider, redis_client)
    first = await service.infer(REQUEST, timeout_s=5)
    second = await service.infer(REQUEST, timeout_s=5)
    assert (first.source, second.source) == (Source.LLM, Source.CACHE)
    assert second.cache_hit
    assert provider.calls == 1


@pytest.mark.parametrize("temperature", [0.0, 1.0])
async def test_different_config_is_not_coalesced(redis_client: Redis, temperature: float) -> None:
    """R1-AC10: una config distinta es otra clave y otra llamada."""
    provider = ScriptedProvider()
    service = make_service(provider, redis_client)
    await service.infer(REQUEST, timeout_s=5)
    other = LLMRequest(model=REQUEST.model, prompt=REQUEST.prompt, temperature=temperature)
    assert (await service.infer(other, timeout_s=5)).source is Source.LLM
    assert provider.calls == 2
