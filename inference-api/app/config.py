"""Configuración inmutable de la aplicación.

Toda perilla operativa vive acá, se lee de variables de entorno (12-factor) y tiene un default
seguro para que `docker compose up` funcione sin configuración adicional.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LLMProviderName = Literal["mock", "openai"]


def _split_csv(value: object) -> object:
    """Permite listas en env vars como `a,b,c` además de JSON."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


DEMO_API_KEYS = "demo-client:demo-key-123,acme:acme-key-456"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", frozen=True, extra="ignore")

    # ── Servicio ────────────────────────────────────────────────────────────────
    service_name: str = "inference-api"
    environment: str = "local"
    log_level: str = "INFO"
    max_inflight_requests: int = Field(default=512, ge=1)
    max_input_chars: int = Field(default=16_000, ge=1)

    # ── Autenticación ───────────────────────────────────────────────────────────
    # Formato: "client_id:api_key,client_id2:api_key2". En producción vendría de un secret manager.
    api_keys: SecretStr = SecretStr(DEMO_API_KEYS)
    # Si se define, `/metrics` exige `Authorization: Bearer <token>` (deploys públicos).
    metrics_token: SecretStr | None = None

    # ── Redis ───────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_socket_timeout_s: float = Field(default=0.25, gt=0)
    redis_max_connections: int = Field(default=200, ge=1)
    redis_failure_threshold: int = Field(default=3, ge=1)
    redis_cooldown_s: float = Field(default=5.0, gt=0)

    # ── Rate limit ──────────────────────────────────────────────────────────────
    rate_limit_requests: int = Field(default=60, ge=1)
    rate_limit_window_s: int = Field(default=60, ge=1)

    # ── Cache + single-flight ───────────────────────────────────────────────────
    cache_ttl_s: int = Field(default=3600, ge=1)
    single_flight_lock_ttl_s: float = Field(default=35.0, gt=0)
    single_flight_wait_s: float = Field(default=30.0, gt=0)

    # ── LLM ─────────────────────────────────────────────────────────────────────
    llm_provider: LLMProviderName = "mock"
    llm_allowed_models: Annotated[tuple[str, ...], NoDecode] = (
        "gpt-4o",
        "gpt-4o-mini",
        "mock-1",
    )
    llm_default_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_timeout_s: float = Field(default=120.0, gt=0)
    llm_max_concurrency: int = Field(default=256, ge=1)
    llm_queue_timeout_s: float = Field(default=2.0, gt=0)
    llm_max_retries: int = Field(default=1, ge=0)
    llm_retry_base_delay_s: float = Field(default=0.1, gt=0)
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"

    # Mock provider: permite simular latencia y fallas para pruebas de resiliencia.
    mock_first_token_latency_s: float = Field(default=0.05, ge=0)
    mock_token_latency_s: float = Field(default=0.01, ge=0)
    mock_failure_rate: float = Field(default=0.0, ge=0, le=1)

    # ── Circuit breaker ─────────────────────────────────────────────────────────
    cb_failure_threshold: int = Field(default=5, ge=1)
    cb_window_s: float = Field(default=30.0, gt=0)
    cb_open_s: float = Field(default=60.0, gt=0)

    # ── Streaming + auditoría ───────────────────────────────────────────────────
    stream_buffer_size: int = Field(default=64, ge=1)
    stream_slow_consumer_timeout_s: float = Field(default=10.0, gt=0)
    audit_queue_size: int = Field(default=10_000, ge=1)
    audit_stream_maxlen: int = Field(default=100_000, ge=1)

    # ── ML ──────────────────────────────────────────────────────────────────────
    ml_models_dir: str = "models"
    ml_default_version: str = "v2"
    ml_max_threads: int = Field(default=4, ge=1)
    ml_batching_enabled: bool = True
    ml_batch_window_ms: float = Field(default=10.0, gt=0)
    ml_max_batch_size: int = Field(default=64, ge=1)

    @model_validator(mode="after")
    def _no_demo_keys_in_production(self) -> Settings:
        """Las keys de demo están en el repo: en producción la réplica no arranca con ellas."""
        if self.environment == "production" and self.api_keys.get_secret_value() == DEMO_API_KEYS:
            raise ValueError("ENVIRONMENT=production requiere API_KEYS propias (no las de demo)")
        return self

    @field_validator("llm_allowed_models", mode="before")
    @classmethod
    def _parse_models(cls, value: object) -> object:
        return _split_csv(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
