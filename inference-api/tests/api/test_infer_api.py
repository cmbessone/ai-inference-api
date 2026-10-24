"""Tests de contrato HTTP de `POST /v1/infer` (R1)."""

from __future__ import annotations

import uuid

import httpx
from redis.asyncio import Redis

from app.config import Settings
from app.core.cache import cache_key
from tests.conftest import AUTH, AppFactory, serve
from tests.fakes import ScriptedProvider, provider_down

BODY = {"model": "gpt-4o", "input": "Hola", "config": {"temperature": 0.7}}


async def test_missing_api_key_is_401(client: httpx.AsyncClient) -> None:
    """R1-AC01."""
    response = await client.post("/v1/infer", json=BODY)
    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized", "detail": "API key ausente o inválida"}


async def test_invalid_key_is_401_and_consumes_no_quota(
    client: httpx.AsyncClient, redis_client: Redis
) -> None:
    """R1-AC02."""
    response = await client.post("/v1/infer", json=BODY, headers={"X-API-Key": "nope"})
    assert response.status_code == 401
    assert await redis_client.keys("ratelimit:*") == []


async def test_success_contract(client: httpx.AsyncClient) -> None:
    """R1-AC03 + R1-AC07 + R1-AC13."""
    response = await client.post("/v1/infer", json=BODY, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert uuid.UUID(body["request_id"])
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["output"] == "Hola mundo"
    assert body["model"] == "gpt-4o"
    assert body["usage"] == {"tokens_in": 1, "tokens_out": 2}
    assert isinstance(body["latency_ms"], int)
    assert body["cache_hit"] is False
    assert response.headers["x-ratelimit-limit"] == "60"
    assert response.headers["x-ratelimit-remaining"] == "59"


async def test_repeated_request_hits_cache(
    client: httpx.AsyncClient, provider: ScriptedProvider
) -> None:
    """R1-AC09."""
    await client.post("/v1/infer", json=BODY, headers=AUTH)
    second = await client.post("/v1/infer", json=BODY, headers=AUTH)
    assert second.json()["cache_hit"] is True
    assert provider.calls == 1


async def test_cache_ttl_is_configurable(
    make_app: AppFactory, settings: Settings, redis_client: Redis
) -> None:
    """R1-AC11."""
    app = make_app(settings_override=settings.model_copy(update={"cache_ttl_s": 42}))
    async with serve(app) as http:
        await http.post("/v1/infer", json=BODY, headers=AUTH)
    key = cache_key("gpt-4o", "Hola", {"temperature": 0.7, "max_tokens": 256})
    assert 41_000 < await redis_client.pttl(key) <= 42_000


async def test_rate_limit_61st_request_is_429(make_app: AppFactory, settings: Settings) -> None:
    """R1-AC04: con límite N, el request N+1 es 429 con retry_after_s y Retry-After."""
    app = make_app(settings_override=settings.model_copy(update={"rate_limit_requests": 3}))
    async with serve(app) as http:
        for i in range(3):
            ok = await http.post("/v1/infer", json={**BODY, "input": f"q{i}"}, headers=AUTH)
            assert ok.status_code == 200
        response = await http.post("/v1/infer", json=BODY, headers=AUTH)

    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "rate_limit_exceeded"
    assert 59 <= body["retry_after_s"] <= 60
    assert response.headers["retry-after"] == str(body["retry_after_s"])


async def test_unknown_model_is_400(client: httpx.AsyncClient) -> None:
    """R1-AC14."""
    response = await client.post("/v1/infer", json={**BODY, "model": "gpt-9"}, headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_model"


async def test_invalid_body_is_422(client: httpx.AsyncClient) -> None:
    """R1-AC14."""
    bad = {**BODY, "config": {"temperature": 9}}
    response = await client.post("/v1/infer", json=bad, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    assert response.json()["detail"][0]["field"] == "config.temperature"


async def test_open_circuit_is_503_without_calling_provider(
    make_app: AppFactory, settings: Settings
) -> None:
    """R1-AC15 (sin reintentos: cada request fallido = 1 fallo del breaker)."""
    provider = ScriptedProvider(error=provider_down())
    no_retry = settings.model_copy(update={"llm_max_retries": 0})
    async with serve(make_app(llm=provider, settings_override=no_retry)) as http:
        for i in range(5):
            failed = await http.post("/v1/infer", json={**BODY, "input": f"x{i}"}, headers=AUTH)
            assert failed.status_code == 502
        calls_before = provider.calls
        response = await http.post("/v1/infer", json=BODY, headers=AUTH)

    assert response.status_code == 503
    assert response.json() == {"error": "llm_unavailable", "retry_after_s": 60}
    assert response.headers["retry-after"] == "60"
    assert provider.calls == calls_before


async def test_timeout_is_504(make_app: AppFactory) -> None:
    """R1-AC16: timeout configurable por request."""
    async with serve(make_app(llm=ScriptedProvider(delay_s=1))) as http:
        response = await http.post("/v1/infer", json={**BODY, "timeout_s": 0.05}, headers=AUTH)
    assert response.status_code == 504
    assert response.json()["error"] == "llm_timeout"
