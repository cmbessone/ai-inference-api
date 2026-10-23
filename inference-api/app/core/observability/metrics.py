"""Métricas Prometheus.

Se usa un `CollectorRegistry` propio por instancia (no el global) para que cada app/test tenga
sus métricas aisladas. Una réplica = un proceso uvicorn, así que no hace falta el modo
multiproceso de prometheus_client: Prometheus scrapea cada réplica por separado (ADR-011).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1, 2.5, 5, 10, 30)
_CIRCUIT_VALUES = {"closed": 0, "half-open": 1, "open": 2}


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.http_requests = Counter(
            "http_requests_total", "Requests HTTP", ["method", "route", "status"], registry=r
        )
        self.http_latency = Histogram(
            "http_request_duration_seconds",
            "Latencia HTTP",
            ["method", "route"],
            buckets=_LATENCY_BUCKETS,
            registry=r,
        )
        self.inflight = Gauge("http_inflight_requests", "Requests en curso", registry=r)
        self.load_shed = Counter("load_shed_total", "Requests rechazados por carga", registry=r)
        self.cache_requests = Counter(
            "cache_requests_total", "Lookups de cache", ["result"], registry=r
        )
        self.coalesced = Counter(
            "llm_coalesced_requests_total",
            "Requests resueltos por single-flight sin llamar al LLM",
            ["scope"],
            registry=r,
        )
        self.llm_calls = Counter(
            "llm_calls_total", "Llamadas al proveedor LLM", ["provider", "outcome"], registry=r
        )
        self.llm_tokens = Counter(
            "llm_tokens_total", "Tokens procesados", ["direction"], registry=r
        )
        self.circuit_state = Gauge(
            "llm_circuit_state", "Estado del circuito (0=closed, 1=half-open, 2=open)", registry=r
        )
        self.rate_limited = Counter("rate_limit_rejections_total", "Requests 429", registry=r)
        self.redis_fallback = Counter(
            "redis_fallback_total",
            "Operaciones degradadas por Redis caído",
            ["component"],
            registry=r,
        )
        self.stream_cancelled = Counter(
            "stream_cancelled_total", "Streams terminados antes de tiempo", ["reason"], registry=r
        )
        self.audit_dropped = Counter(
            "audit_dropped_total", "Registros de auditoría descartados", registry=r
        )
        self.classify_batch_size = Histogram(
            "classify_batch_size",
            "Tamaño de batch de clasificación",
            buckets=(1, 2, 4, 8, 16, 32, 64, 128),
            registry=r,
        )

    # ── Helpers con nombre de dominio ──────────────────────────────────────────

    def observe(self, method: str, route: str, status_code: int, duration_s: float) -> None:
        """Implementa `RequestObserver` para el middleware de contexto."""
        self.http_requests.labels(method, route, str(status_code)).inc()
        self.http_latency.labels(method, route).observe(duration_s)

    def set_circuit_state(self, state: str) -> None:
        self.circuit_state.set(_CIRCUIT_VALUES.get(state, 0))

    def render(self) -> bytes:
        return generate_latest(self.registry)
