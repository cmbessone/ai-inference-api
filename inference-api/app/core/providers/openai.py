"""Adaptador para la API de OpenAI (Chat Completions) sobre `httpx.AsyncClient`.

Un único cliente HTTP con pool de conexiones por proceso (keep-alive): abrir una conexión TLS
por request agregaría decenas de ms al P95.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.errors import LLMProviderError
from app.core.ports import Completion, LLMRequest, Usage

_SSE_PREFIX = "data: "
_SSE_DONE = "[DONE]"


def build_payload(request: LLMRequest, *, stream: bool) -> dict[str, Any]:
    return {
        "model": request.model,
        "messages": [{"role": "user", "content": request.prompt}],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "stream": stream,
    }


def classify_status(status_code: int) -> LLMProviderError:
    """5xx/429 indican problemas del proveedor (cuentan para el breaker); 4xx son del request."""
    provider_side = status_code >= 500 or status_code == 429
    return LLMProviderError(
        f"openai respondió {status_code}",
        retryable=provider_side,
        counts_as_failure=provider_side,
    )


def parse_stream_line(line: str) -> str | None:
    """Extrae el delta de texto de una línea SSE de OpenAI (pura). `None` si no hay texto."""
    if not line.startswith(_SSE_PREFIX):
        return None
    data = line.removeprefix(_SSE_PREFIX).strip()
    if data == _SSE_DONE:
        return None
    choices = json.loads(data).get("choices") or [{}]
    content: str | None = choices[0].get("delta", {}).get("content")
    return content or None


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str, max_connections: int = 256) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            limits=httpx.Limits(
                max_connections=max_connections, max_keepalive_connections=max_connections
            ),
            timeout=httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0),
        )

    async def complete(self, request: LLMRequest) -> Completion:
        try:
            response = await self._client.post(
                "/chat/completions", json=build_payload(request, stream=False)
            )
        except httpx.TransportError as exc:
            raise LLMProviderError(str(exc), retryable=True, counts_as_failure=True) from exc
        if response.status_code >= 400:
            raise classify_status(response.status_code)
        body = response.json()
        usage = body.get("usage", {})
        return Completion(
            text=body["choices"][0]["message"]["content"],
            usage=Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        try:
            # Al salir del `async with` (fin, error o cancelación) httpx cierra la conexión:
            # así se cancela el request upstream cuando el cliente se desconecta (R2-AC03).
            async with self._client.stream(
                "POST", "/chat/completions", json=build_payload(request, stream=True)
            ) as response:
                if response.status_code >= 400:
                    raise classify_status(response.status_code)
                async for line in response.aiter_lines():
                    token = parse_stream_line(line)
                    if token is not None:
                        yield token
        except httpx.TransportError as exc:
            raise LLMProviderError(str(exc), retryable=False, counts_as_failure=True) from exc

    async def aclose(self) -> None:
        await self._client.aclose()
