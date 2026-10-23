from __future__ import annotations

import asyncio

import pytest

from app.core.circuit_breaker import (
    BreakerPolicy,
    CircuitBreaker,
    CircuitState,
    InMemoryBreakerStore,
)
from app.core.clock import SystemClock
from app.core.errors import (
    LLMProviderError,
    LLMTimeoutError,
    LLMUnavailableError,
    OverloadedError,
)
from app.core.llm_client import ResilientLLMClient, RetryPolicy, backoff_delay
from app.core.observability.metrics import Metrics
from app.core.ports import LLMRequest
from tests.fakes import ScriptedProvider, provider_down

REQUEST = LLMRequest(model="gpt-4o", prompt="hola")


def make_client(
    provider: ScriptedProvider,
    *,
    max_retries: int = 0,
    max_concurrency: int = 10,
    queue_timeout_s: float = 1.0,
) -> tuple[ResilientLLMClient, CircuitBreaker]:
    clock = SystemClock()
    breaker = CircuitBreaker(InMemoryBreakerStore(BreakerPolicy()), clock)
    client = ResilientLLMClient(
        provider,
        breaker,
        clock,
        Metrics(),
        max_concurrency=max_concurrency,
        queue_timeout_s=queue_timeout_s,
        retry=RetryPolicy(max_retries=max_retries, base_delay_s=0.001),
    )
    return client, breaker


def test_backoff_full_jitter_is_bounded() -> None:
    policy = RetryPolicy(base_delay_s=0.1, max_delay_s=1.0)
    assert backoff_delay(0, policy, rand=1.0) == pytest.approx(0.1)
    assert backoff_delay(3, policy, rand=0.5) == pytest.approx(0.4)
    assert backoff_delay(10, policy, rand=1.0) == 1.0
    assert backoff_delay(2, policy, rand=0.0) == 0.0


async def test_timeout_raises_and_counts_as_failure() -> None:
    """R1-AC16: timeout por request → LLMTimeoutError."""
    client, _ = make_client(ScriptedProvider(delay_s=1.0))
    with pytest.raises(LLMTimeoutError):
        await client.complete(REQUEST, timeout_s=0.02)


async def test_circuit_opens_after_five_failures_and_rejects_fast() -> None:
    """R1-AC15 + RS-AC01/02: al 6.º request no se llama al proveedor."""
    provider = ScriptedProvider(error=provider_down())
    client, breaker = make_client(provider)
    for _ in range(5):
        with pytest.raises(LLMProviderError):
            await client.complete(REQUEST, timeout_s=1)

    with pytest.raises(LLMUnavailableError) as rejected:
        await client.complete(REQUEST, timeout_s=1)
    assert provider.calls == 5
    assert rejected.value.retry_after_s == 60
    assert (await breaker.status()).state is CircuitState.OPEN


async def test_client_errors_do_not_open_circuit() -> None:
    """RS-AC05: 4xx del proveedor no cuentan como fallo."""
    bad_request = LLMProviderError("400", retryable=False, counts_as_failure=False)
    client, breaker = make_client(ScriptedProvider(error=bad_request))
    for _ in range(10):
        with pytest.raises(LLMProviderError):
            await client.complete(REQUEST, timeout_s=1)
    assert (await breaker.status()).state is CircuitState.CLOSED


async def test_transient_error_is_retried() -> None:
    """RS-AC12: un error transitorio se reintenta con backoff."""
    provider = ScriptedProvider(error=provider_down(), fail_first=1)
    client, _ = make_client(provider, max_retries=1)
    completion = await client.complete(REQUEST, timeout_s=1)
    assert completion.text == "Hola mundo"
    assert provider.calls == 2


async def test_no_retry_for_non_retryable_errors() -> None:
    error = LLMProviderError("400", retryable=False, counts_as_failure=False)
    provider = ScriptedProvider(error=error)
    client, _ = make_client(provider, max_retries=3)
    with pytest.raises(LLMProviderError):
        await client.complete(REQUEST, timeout_s=1)
    assert provider.calls == 1


async def test_bulkhead_rejects_when_saturated() -> None:
    """RS-AC09: con la capacidad agotada, el exceso falla con overloaded."""
    client, _ = make_client(ScriptedProvider(delay_s=0.2), max_concurrency=1, queue_timeout_s=0.01)
    first = asyncio.create_task(client.complete(REQUEST, timeout_s=1))
    await asyncio.sleep(0.01)
    with pytest.raises(OverloadedError):
        await client.complete(REQUEST, timeout_s=1)
    await first
