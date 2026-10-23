"""DTOs del contrato HTTP público (separados de los tipos de dominio)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.core.ports import LLMRequest


class InferConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=1, le=4096)


class InferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=128, examples=["gpt-4o"])
    input: str = Field(min_length=1, examples=["Explicá qué es un circuit breaker"])
    config: InferConfig = Field(default_factory=InferConfig)
    timeout_s: float | None = Field(default=None, gt=0, description="Default: 30 s")

    def to_domain(self) -> LLMRequest:
        return LLMRequest(
            model=self.model,
            prompt=self.input,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )


class UsageOut(BaseModel):
    tokens_in: int
    tokens_out: int


class InferResponse(BaseModel):
    request_id: uuid.UUID | str
    output: str
    model: str
    usage: UsageOut
    latency_ms: int
    cache_hit: bool


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    retry_after_s: int | None = None
