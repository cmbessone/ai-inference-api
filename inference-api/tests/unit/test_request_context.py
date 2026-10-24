from __future__ import annotations

import uuid

import httpx
import pytest

from app.api.middleware.request_context import classify_outcome, resolve_request_id


def test_resolve_request_id_reuses_safe_incoming_id() -> None:
    """OB-AC02: se respeta un X-Request-ID entrante válido."""
    assert resolve_request_id("trace-abc-12345") == "trace-abc-12345"


@pytest.mark.parametrize("incoming", [None, "", "short", "has spaces in it", "x" * 200, "inj\nect"])
def test_resolve_request_id_generates_uuid_for_unsafe_values(incoming: str | None) -> None:
    """OB-AC02: valores ausentes o inseguros se reemplazan por un UUID4."""
    assert uuid.UUID(resolve_request_id(incoming)).version == 4


@pytest.mark.parametrize(
    ("status", "override", "expected"),
    [
        (200, None, "success"),
        (429, None, "error"),
        (500, None, "error"),
        (504, None, "timeout"),
        (200, "timeout", "timeout"),
    ],
)
def test_classify_outcome(status: int, override: str | None, expected: str) -> None:
    """OB-AC01: el campo `status` del log es success | error | timeout."""
    assert classify_outcome(status, override) == expected  # type: ignore[arg-type]


async def test_response_echoes_request_id(client: httpx.AsyncClient) -> None:
    """OB-AC02: el request_id vuelve en el header de respuesta."""
    response = await client.get("/health/live", headers={"X-Request-ID": "my-trace-0001"})
    assert response.headers["x-request-id"] == "my-trace-0001"


async def test_liveness(client: httpx.AsyncClient) -> None:
    """OB-AC05: liveness no depende de nada externo."""
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
