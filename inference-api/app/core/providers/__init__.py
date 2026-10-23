"""Factory de proveedores LLM: el resto del sistema sólo ve el puerto `LLMProvider`."""

from __future__ import annotations

from app.config import Settings
from app.core.ports import LLMProvider
from app.core.providers.mock import MockProvider
from app.core.providers.openai import OpenAIProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "openai":
        if settings.openai_api_key is None:
            raise ValueError("LLM_PROVIDER=openai requiere OPENAI_API_KEY")
        return OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            max_connections=settings.llm_max_concurrency,
        )
    return MockProvider(
        first_token_latency_s=settings.mock_first_token_latency_s,
        token_latency_s=settings.mock_token_latency_s,
        failure_rate=settings.mock_failure_rate,
    )
