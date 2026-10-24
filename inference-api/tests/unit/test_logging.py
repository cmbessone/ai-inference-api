from __future__ import annotations

from structlog.testing import capture_logs

from app.config import Settings
from app.core.observability.logging import redact_sensitive
from tests.conftest import API_KEY, AUTH, AppFactory, serve


def test_redact_sensitive_masks_secrets() -> None:
    """OB-AC03: claves sensibles enmascaradas antes de serializar."""
    event = {"api_key": "s3cr3t", "Authorization": "Bearer x", "client_id": "acme"}
    assert redact_sensitive(None, "info", event) == {
        "api_key": "***",
        "Authorization": "***",
        "client_id": "acme",
    }


async def test_access_log_has_required_fields_and_no_secrets(
    make_app: AppFactory, settings: Settings
) -> None:
    """OB-AC01 + OB-AC03: una línea por request, campos mínimos, sin key ni prompt."""
    prompt = "prompt-confidencial-xyz"
    app = make_app(settings_override=settings.model_copy(update={"log_level": "INFO"}))
    async with serve(app) as http:
        with capture_logs() as logs:
            await http.post("/v1/infer", json={"model": "gpt-4o", "input": prompt}, headers=AUTH)

    access = [entry for entry in logs if entry["event"] == "request"]
    assert len(access) == 1
    entry = access[0]
    for field in ("request_id", "client_id", "model", "latency_ms", "cache_hit", "status"):
        assert field in entry
    assert entry["client_id"] == "demo-client"
    assert entry["status"] == "success"
    serialized = repr(logs)
    assert API_KEY not in serialized
    assert prompt not in serialized
