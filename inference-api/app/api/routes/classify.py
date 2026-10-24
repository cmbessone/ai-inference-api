"""`POST /v1/classify` — modelo ML propio con versionado por header (R3)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import ContainerDep
from app.api.schemas import ErrorResponse
from app.core.observability.context import current_context

router = APIRouter(tags=["classification"])


class ClassifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input: str = Field(min_length=1, max_length=10_000, examples=["me encantó el producto"])


class ClassifyResponse(BaseModel):
    label: str
    confidence: float = Field(ge=0, le=1)
    model_version: str
    latency_ms: int


@router.post(
    "/classify",
    response_model=ClassifyResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def classify(
    body: ClassifyRequest,
    container: ContainerDep,
    x_model_version: Annotated[str | None, Header(description="v1 | v2 (default v2)")] = None,
) -> ClassifyResponse:
    result = await container.classification.classify(body.input, x_model_version)
    if (ctx := current_context()) is not None:
        ctx.model = f"classifier-{result.version}"
    return ClassifyResponse(
        label=result.prediction.label,
        confidence=result.prediction.confidence,
        model_version=result.version,
        latency_ms=result.latency_ms,
    )
