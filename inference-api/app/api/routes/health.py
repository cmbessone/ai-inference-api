"""Healthchecks: liveness (proceso vivo) separado de readiness (listo para tráfico)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Liveness (OB-AC05): no toca dependencias; si Redis cae no queremos que reinicien el pod."""
    return {"status": "ok"}
