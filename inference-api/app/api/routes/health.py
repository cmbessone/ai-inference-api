"""Healthchecks: liveness, readiness y estado detallado (OB-AC04..06).

- `/health/live`  → ¿el proceso responde? No toca dependencias: si Redis cae no queremos que el
  orquestador reinicie todas las réplicas (eso convertiría una degradación en una caída total).
- `/health/ready` → ¿debe recibir tráfico? No mientras carga modelos ni durante el shutdown.
- `/health`       → estado detallado para humanos y dashboards.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.api.dependencies import ContainerDep
from app.core.circuit_breaker import CircuitState

router = APIRouter(tags=["health"])


class HealthReport(BaseModel):
    status: Literal["ok", "degraded", "down"]
    redis: Literal["ok", "down"]
    llm_circuit: CircuitState
    models_loaded: list[str]


def overall_status(
    redis_ok: bool, circuit: CircuitState, models: list[str]
) -> Literal["ok", "degraded", "down"]:
    """Pura: sin modelos la réplica no sirve; sin Redis o con el circuito abierto, degrada."""
    if not models:
        return "down"
    if not redis_ok or circuit is not CircuitState.CLOSED:
        return "degraded"
    return "ok"


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(container: ContainerDep, response: Response) -> dict[str, str]:
    lifecycle = container.lifecycle
    if lifecycle.ready and not lifecycle.shutting_down:
        return {"status": "ready"}
    response.status_code = 503
    return {"status": "shutting_down" if lifecycle.shutting_down else "starting"}


@router.get("/health", response_model=HealthReport)
async def health(container: ContainerDep, response: Response) -> HealthReport:
    redis_ok = await container.redis.ping()
    circuit = await container.llm.circuit_state()
    models = container.classification.versions
    status = overall_status(redis_ok, circuit, models)
    if status == "down":
        response.status_code = 503
    return HealthReport(
        status=status,
        redis="ok" if redis_ok else "down",
        llm_circuit=circuit,
        models_loaded=models,
    )
