"""`POST /v1/infer` — inferencia sincrónica (R1)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import ContainerDep
from app.api.middleware.request_context import current_context
from app.api.schemas import ErrorResponse, InferRequest, InferResponse, UsageOut
from app.container import Container
from app.core.errors import UnknownModelError

router = APIRouter(tags=["inference"])

_ERRORS: dict[int | str, dict[str, object]] = {
    code: {"model": ErrorResponse} for code in (400, 401, 429, 502, 503, 504)
}


def ensure_model_allowed(model: str, container: Container) -> None:
    allowed = container.settings.llm_allowed_models
    if model not in allowed:
        raise UnknownModelError(f"Modelo '{model}' no soportado. Disponibles: {', '.join(allowed)}")


def resolve_timeout(requested: float | None, default: float, maximum: float) -> float:
    """Timeout efectivo (pura): el del request acotado por el máximo del servicio."""
    return min(requested or default, maximum)


@router.post("/infer", response_model=InferResponse, responses=_ERRORS)
async def infer(body: InferRequest, container: ContainerDep) -> InferResponse:
    ensure_model_allowed(body.model, container)
    ctx = current_context()
    if ctx is not None:
        ctx.model = body.model

    settings = container.settings
    started = container.clock.monotonic()
    result = await container.inference.infer(
        body.to_domain(),
        timeout_s=resolve_timeout(
            body.timeout_s, settings.llm_default_timeout_s, settings.llm_max_timeout_s
        ),
    )
    latency_ms = round((container.clock.monotonic() - started) * 1000)

    if ctx is not None:
        ctx.cache_hit = result.cache_hit
    return InferResponse(
        request_id=ctx.request_id if ctx else "",
        output=result.completion.text,
        model=body.model,
        usage=UsageOut(
            tokens_in=result.completion.usage.tokens_in,
            tokens_out=result.completion.usage.tokens_out,
        ),
        latency_ms=latency_ms,
        cache_hit=result.cache_hit,
    )
