"""Operación: health, métricas, load shedding y degradación sin Redis (OB/RS)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.api.middleware.load_shedding import is_exempt
from app.api.routes.health import overall_status
from app.config import Settings
from app.core.circuit_breaker import CircuitState
from tests.conftest import AUTH, AppFactory, serve
from tests.fakes import ScriptedProvider, provider_down, unreachable_redis

BODY = {"model": "gpt-4o", "input": "Hola"}


@pytest.mark.parametrize(
    ("redis_ok", "circuit", "models", "expected"),
    [
        (True, CircuitState.CLOSED, ["v1"], "ok"),
        (False, CircuitState.CLOSED, ["v1"], "degraded"),
        (True, CircuitState.OPEN, ["v1"], "degraded"),
        (True, CircuitState.CLOSED, [], "down"),
    ],
)
def test_overall_status(
    redis_ok: bool, circuit: CircuitState, models: list[str], expected: str
) -> None:
    """OB-AC04."""
    assert overall_status(redis_ok, circuit, models) == expected


async def test_health_report(client: httpx.AsyncClient) -> None:
    """OB-AC04: contrato exacto del enunciado."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "redis": "ok",
        "llm_circuit": "closed",
        "models_loaded": ["v1", "v2"],
    }


async def test_readiness_and_liveness(client: httpx.AsyncClient) -> None:
    """OB-AC05 + OB-AC06."""
    assert (await client.get("/health/live")).status_code == 200
    assert (await client.get("/health/ready")).json() == {"status": "ready"}


async def test_metrics_exposes_required_series(client: httpx.AsyncClient) -> None:
    """OB-AC07: requests, latencia, cache hit/miss, circuito, rate limit."""
    await client.post("/v1/infer", json=BODY, headers=AUTH)
    await client.post("/v1/infer", json=BODY, headers=AUTH)
    text = (await client.get("/metrics")).text
    assert 'http_requests_total{method="POST",route="/v1/infer",status="200"} 2.0' in text
    assert "http_request_duration_seconds_bucket" in text
    assert 'cache_requests_total{result="hit"} 1.0' in text
    assert 'cache_requests_total{result="miss"} 1.0' in text
    assert "llm_circuit_state 0.0" in text


async def test_degrades_gracefully_without_redis(make_app: AppFactory) -> None:
    """R1-AC08 + R1-AC12 + RS-AC07: sin Redis la API sigue respondiendo."""
    async with serve(make_app(redis=unreachable_redis())) as http:
        infer = await http.post("/v1/infer", json=BODY, headers=AUTH)
        health = await http.get("/health")
        metrics = (await http.get("/metrics")).text

    assert infer.status_code == 200
    assert infer.json()["cache_hit"] is False
    assert health.status_code == 200
    assert health.json()["status"] == "degraded"
    assert health.json()["redis"] == "down"
    assert 'redis_fallback_total{component="rate_limit"}' in metrics


def test_health_and_metrics_are_never_shed() -> None:
    assert is_exempt("/health/ready")
    assert is_exempt("/metrics")
    assert not is_exempt("/v1/infer")


async def test_load_shedding_rejects_excess(make_app: AppFactory, settings: Settings) -> None:
    """RS-AC10: con la réplica saturada responde 503 inmediato con Retry-After."""
    limited = settings.model_copy(update={"max_inflight_requests": 2})
    slow = ScriptedProvider(delay_s=0.2)
    async with serve(make_app(settings_override=limited, llm=slow)) as http:
        busy = [
            asyncio.create_task(
                http.post("/v1/infer", json={**BODY, "input": f"q{i}"}, headers=AUTH)
            )
            for i in range(2)
        ]
        await asyncio.sleep(0.05)
        shed = await http.post("/v1/infer", json={**BODY, "input": "extra"}, headers=AUTH)
        health = await http.get("/health/live")
        done = await asyncio.gather(*busy)

    assert shed.status_code == 503
    assert shed.json() == {"error": "overloaded"}
    assert shed.headers["retry-after"] == "1"
    assert health.status_code == 200
    assert all(r.status_code == 200 for r in done)


async def test_readiness_fails_during_shutdown(make_app: AppFactory) -> None:
    """OB-AC06 + RS-AC13: al recibir SIGTERM la réplica deja de declararse lista."""
    app = make_app()
    async with serve(app) as http:
        app.state.container.lifecycle.shutting_down = True
        response = await http.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "shutting_down"}


async def test_breaker_policy_is_configurable(make_app: AppFactory, settings: Settings) -> None:
    """RS-AC08: umbral/ventana/duración salen de la configuración."""
    custom = settings.model_copy(
        update={"cb_failure_threshold": 2, "cb_open_s": 15, "llm_max_retries": 0}
    )
    provider = ScriptedProvider(error=provider_down())
    async with serve(make_app(settings_override=custom, llm=provider)) as http:
        for i in range(2):
            await http.post("/v1/infer", json={**BODY, "input": f"x{i}"}, headers=AUTH)
        response = await http.post("/v1/infer", json=BODY, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["retry_after_s"] == 15


async def test_metrics_can_require_a_token(make_app: AppFactory, settings: Settings) -> None:
    """OB-AC07 en deploys públicos: con METRICS_TOKEN, /metrics exige Bearer."""
    secured = settings.model_copy(update={"metrics_token": SecretStr("s3cr3t")})
    async with serve(make_app(settings_override=secured)) as http:
        anonymous = await http.get("/metrics")
        wrong = await http.get("/metrics", headers={"Authorization": "Bearer nope"})
        ok = await http.get("/metrics", headers={"Authorization": "Bearer s3cr3t"})
    assert (anonymous.status_code, wrong.status_code, ok.status_code) == (401, 401, 200)


def test_production_refuses_demo_api_keys() -> None:
    """Las keys de demo son públicas (están en el repo): en producción no se arranca."""
    with pytest.raises(ValidationError, match="API_KEYS propias"):
        Settings(environment="production")
    assert Settings(environment="production", api_keys=SecretStr("c:una-key-larga")).api_keys
