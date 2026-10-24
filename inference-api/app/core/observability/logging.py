"""Logging estructurado en JSON con contexto por request.

El contexto (`request_id`, `client_id`, …) se guarda en `contextvars`, que asyncio copia a cada
tarea: así cualquier log emitido durante un request lleva su `request_id` sin pasarlo a mano.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SENSITIVE_KEYS = frozenset({"api_key", "x-api-key", "authorization", "password", "secret"})


def redact_sensitive(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Procesador puro: enmascara claves sensibles antes de serializar (OB-AC03)."""
    return {k: ("***" if k.lower() in _SENSITIVE_KEYS else v) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO") -> None:
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    structlog.configure(
        processors=[*shared, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        # Sin cache: permite reconfigurar (nivel en runtime, captura en tests) a costo de µs.
        cache_logger_on_first_use=False,
    )
    # Uvicorn ya tiene su access log; lo silenciamos porque emitimos el nuestro en JSON.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
