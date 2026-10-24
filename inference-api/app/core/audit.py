"""Auditoría de streams sin bloquear la respuesta (R2-AC05).

Productor/consumidor con cola acotada:
- `submit()` es síncrono y O(1): encola o, si la cola está llena, descarta y cuenta la pérdida.
  El request **nunca** espera a la auditoría (load shedding de un trabajo secundario).
- Un worker en background (arrancado en el `lifespan`) consume la cola y escribe en un
  Redis Stream (`XADD ... MAXLEN ~`), de donde un consumidor externo puede llevarlo a un data lake.
- En el apagado se vacía la cola con un tiempo máximo (graceful shutdown, RS-AC13).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass
from typing import Protocol

from app.core.errors import BackendUnavailableError
from app.core.observability.logging import get_logger
from app.core.observability.metrics import Metrics
from app.core.redis_gateway import RedisGateway

logger = get_logger("audit")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    request_id: str
    client_id: str
    model: str
    prompt: str
    output: str
    tokens_out: int
    duration_ms: int
    end_reason: str
    timestamp: float


def to_fields(record: AuditRecord) -> dict[str, str]:
    """Redis Streams sólo acepta valores planos (pura)."""
    return {k: str(v) for k, v in asdict(record).items()}


class AuditSink(Protocol):
    def submit(self, record: AuditRecord) -> bool: ...


class AuditWriter(Protocol):
    async def write(self, record: AuditRecord) -> None: ...


class RedisStreamAuditWriter:
    def __init__(self, gateway: RedisGateway, stream: str = "audit:streams", maxlen: int = 100_000):
        self._gateway = gateway
        self._stream = stream
        self._maxlen = maxlen

    async def write(self, record: AuditRecord) -> None:
        fields = to_fields(record)
        try:
            await self._gateway.run(
                lambda r: r.xadd(self._stream, fields, maxlen=self._maxlen, approximate=True)  # type: ignore[arg-type]
            )
        except BackendUnavailableError:
            # Sin Redis no se pierde: queda en el log estructurado (que también se recolecta).
            logger.warning("audit_fallback_to_log", **fields)


class QueuedAuditSink:
    def __init__(self, writer: AuditWriter, metrics: Metrics, queue_size: int = 10_000) -> None:
        self._writer = writer
        self._metrics = metrics
        self._queue: asyncio.Queue[AuditRecord] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None

    def submit(self, record: AuditRecord) -> bool:
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            self._metrics.audit_dropped.inc()
            return False
        return True

    def pending(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="audit-worker")

    async def _run(self) -> None:
        while True:
            record = await self._queue.get()
            try:
                await self._writer.write(record)
            except Exception:  # el worker no puede morir por un registro malo
                logger.exception("audit_write_failed", request_id=record.request_id)
            finally:
                self._queue.task_done()

    async def aclose(self, timeout_s: float = 5.0) -> None:
        if self._worker is None:
            return
        try:
            async with asyncio.timeout(timeout_s):
                await self._queue.join()
        except TimeoutError:
            logger.warning("audit_drain_timeout", pending=self._queue.qsize())
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None
