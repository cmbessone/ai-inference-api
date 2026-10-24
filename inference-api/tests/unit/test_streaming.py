"""R2 — streaming SSE: formato, errores, backpressure, cancelación y auditoría."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.audit import AuditRecord, QueuedAuditSink
from app.core.circuit_breaker import BreakerPolicy, CircuitBreaker, InMemoryBreakerStore
from app.core.clock import SystemClock
from app.core.llm_client import ResilientLLMClient, RetryPolicy
from app.core.observability.metrics import Metrics
from app.core.ports import LLMRequest
from app.core.sse import DONE, error_event, token_event
from app.services.streaming import StreamIdentity, StreamingPolicy, StreamingService
from tests.fakes import ScriptedProvider, provider_down

REQUEST = LLMRequest(model="gpt-4o", prompt="hola")
IDENTITY = StreamIdentity(request_id="req-1", client_id="acme")


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def submit(self, record: AuditRecord) -> bool:
        self.records.append(record)
        return True


def make_service(
    provider: ScriptedProvider,
    *,
    buffer_size: int = 64,
    slow_timeout_s: float = 1.0,
    metrics: Metrics | None = None,
) -> tuple[StreamingService, RecordingSink, CircuitBreaker]:
    clock = SystemClock()
    metrics = metrics or Metrics()
    breaker = CircuitBreaker(InMemoryBreakerStore(BreakerPolicy()), clock)
    llm = ResilientLLMClient(
        provider,
        breaker,
        clock,
        metrics,
        max_concurrency=10,
        queue_timeout_s=1,
        retry=RetryPolicy(max_retries=0),
    )
    sink = RecordingSink()
    policy = StreamingPolicy(buffer_size=buffer_size, slow_consumer_timeout_s=slow_timeout_s)
    return StreamingService(llm, sink, clock, metrics, policy), sink, breaker


async def collect(service: StreamingService, timeout_s: float = 5) -> list[bytes]:
    return [event async for event in service.events(REQUEST, timeout_s, IDENTITY)]


def test_sse_format_matches_contract() -> None:
    """R2-AC01: formato exacto del enunciado."""
    assert token_event("Hola", 0) == b'data: {"token": "Hola", "index": 0}\n\n'
    assert DONE == b"data: [DONE]\n\n"
    assert error_event("llm_unavailable", 45) == (
        b'data: {"error": "llm_unavailable", "retry_after_s": 45}\n\n'
    )


async def test_tokens_then_done() -> None:
    """R2-AC01: tokens con índice consecutivo y [DONE] al final."""
    service, sink, _ = make_service(ScriptedProvider(tokens=("Hola", " mundo", "!")))
    events = await collect(service)
    assert events == [token_event("Hola", 0), token_event(" mundo", 1), token_event("!", 2), DONE]
    await asyncio.sleep(0)
    assert sink.records[0].output == "Hola mundo!"
    assert sink.records[0].end_reason == "completed"


async def test_open_circuit_emits_single_error_without_calling_provider() -> None:
    """R2-AC02."""
    provider = ScriptedProvider()
    service, _, breaker = make_service(provider)
    for _ in range(5):
        await breaker.record_failure()

    events = await collect(service)
    assert len(events) == 1
    payload = json.loads(events[0].removeprefix(b"data: "))
    assert payload == {"error": "llm_unavailable", "retry_after_s": 60}
    assert provider.calls == 0


async def test_provider_failure_mid_stream_emits_llm_error() -> None:
    service, sink, _ = make_service(ScriptedProvider(error=provider_down()))
    assert await collect(service) == [error_event("llm_error")]
    assert sink.records[0].end_reason == "llm_error"


async def test_timeout_emits_error_and_cancels_upstream() -> None:
    """R2-AC04: el timeout total corta el upstream y avisa al cliente."""
    provider = ScriptedProvider(tokens=("a", "b", "c"), token_delay_s=0.2)
    service, sink, _ = make_service(provider)
    events = [e async for e in service.events(REQUEST, 0.05, IDENTITY)]
    assert events == [token_event("a", 0), error_event("llm_timeout")]
    assert provider.stream_closed.is_set()
    assert sink.records[0].end_reason == "llm_timeout"


async def test_client_disconnect_cancels_upstream_and_audits() -> None:
    """R2-AC03 + R2-AC05: cerrar el generador (lo que hace Starlette) corta el upstream."""
    metrics = Metrics()
    provider = ScriptedProvider(tokens=tuple(str(i) for i in range(100)), token_delay_s=0.01)
    service, sink, _ = make_service(provider, metrics=metrics)

    events = service.events(REQUEST, 5, IDENTITY)
    assert await anext(events) == token_event("0", 0)
    await events.aclose()  # el cliente se fue

    await asyncio.wait_for(provider.stream_closed.wait(), timeout=1)
    await asyncio.sleep(0)
    assert sink.records[0].end_reason == "client_disconnect"
    assert len(sink.records[0].output) < 100
    disconnects = metrics.stream_cancelled.labels("client_disconnect")._value.get()
    assert disconnects == 1


async def test_backpressure_bounds_buffer_and_aborts_slow_consumer() -> None:
    """R2-AC07 + R2-AC08 (Escenario C): el buffer nunca supera N y el lento se aborta."""
    provider = ScriptedProvider(tokens=tuple(str(i) for i in range(1_000)))
    service, sink, _ = make_service(provider, buffer_size=4, slow_timeout_s=0.05)

    events = service.events(REQUEST, 5, IDENTITY)
    first = await anext(events)  # arranca la productora y luego "no leemos más"
    await asyncio.sleep(0.2)  # cliente lento: más que slow_consumer_timeout
    rest = [e async for e in events]

    assert first == token_event("0", 0)
    assert rest == [error_event("slow_consumer")]
    assert provider.stream_closed.is_set()
    produced = sink.records[0].tokens_out
    assert produced <= 1 + 4 + 1  # leído + buffer + el que no entró: memoria acotada
    assert sink.records[0].end_reason == "slow_consumer"


async def test_audit_sink_never_blocks_and_counts_drops() -> None:
    """R2-AC05: con la cola llena se descarta y se cuenta, sin bloquear."""

    class NeverWriter:
        async def write(self, record: AuditRecord) -> None:
            await asyncio.sleep(10)

    metrics = Metrics()
    sink = QueuedAuditSink(NeverWriter(), metrics, queue_size=2)
    record = AuditRecord("r", "c", "m", "p", "o", 1, 1, "completed", 0.0)
    assert [sink.submit(record) for _ in range(3)] == [True, True, False]
    assert metrics.audit_dropped._value.get() == 1


async def test_audit_worker_drains_on_shutdown() -> None:
    """RS-AC13: al apagar se vacía la cola."""
    written: list[str] = []

    class SlowWriter:
        async def write(self, record: AuditRecord) -> None:
            await asyncio.sleep(0.01)
            written.append(record.request_id)

    sink = QueuedAuditSink(SlowWriter(), Metrics(), queue_size=10)
    sink.start()
    for i in range(5):
        sink.submit(AuditRecord(str(i), "c", "m", "p", "o", 1, 1, "completed", 0.0))
    await sink.aclose(timeout_s=1)
    assert written == ["0", "1", "2", "3", "4"]


@pytest.mark.parametrize("buffer_size", [1, 8])
async def test_fast_consumer_receives_everything(buffer_size: int) -> None:
    tokens = tuple(f"t{i}" for i in range(50))
    service, _, _ = make_service(ScriptedProvider(tokens=tokens), buffer_size=buffer_size)
    events = await collect(service)
    assert len(events) == 51
    assert events[-1] == DONE
