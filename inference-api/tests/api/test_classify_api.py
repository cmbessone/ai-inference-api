"""Contrato HTTP de `POST /v1/classify` (R3)."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import AUTH


@pytest.mark.parametrize("version", ["v1", "v2"])
async def test_classify_with_version_header(client: httpx.AsyncClient, version: str) -> None:
    """R3-AC03 + R3-AC07."""
    response = await client.post(
        "/v1/classify",
        json={"input": "excelente producto, me encantó"},
        headers={**AUTH, "X-Model-Version": version},
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"label", "confidence", "model_version", "latency_ms"}
    assert body["label"] == "positive"
    assert 0 <= body["confidence"] <= 1
    assert body["model_version"] == version
    assert isinstance(body["latency_ms"], int)


async def test_default_version_is_v2(client: httpx.AsyncClient) -> None:
    """R3-AC04."""
    response = await client.post("/v1/classify", json={"input": "pésimo servicio"}, headers=AUTH)
    assert response.json()["model_version"] == "v2"
    assert response.json()["label"] == "negative"


async def test_unknown_version_is_400(client: httpx.AsyncClient) -> None:
    """R3-AC05."""
    response = await client.post(
        "/v1/classify", json={"input": "hola"}, headers={**AUTH, "X-Model-Version": "v9"}
    )
    assert response.status_code == 400
    assert response.json() == {
        "error": "unknown_model_version",
        "detail": "Versión 'v9' no existe. Disponibles: v1, v2",
    }


async def test_classify_requires_api_key(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/classify", json={"input": "hola"})
    assert response.status_code == 401
