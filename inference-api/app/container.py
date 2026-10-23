"""Composition root.

Único lugar donde se instancian adaptadores concretos y se conectan entre sí. El resto del código
depende de puertos (`Protocol`s) y recibe sus dependencias inyectadas: eso permite reemplazar
Redis, el proveedor LLM o el runtime de ML sin tocar la lógica, y testear con dobles.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from redis.asyncio import Redis

from app.config import Settings
from app.core.audit import QueuedAuditSink, RedisStreamAuditWriter
from app.core.auth import KeyStore, StaticKeyStore, parse_api_keys
from app.core.cache import RedisCache, RedisLock
from app.core.circuit_breaker import (
    BreakerPolicy,
    CircuitBreaker,
    FallbackBreakerStore,
    InMemoryBreakerStore,
    RedisBreakerStore,
)
from app.core.clock import Clock, SystemClock
from app.core.llm_client import ResilientLLMClient, RetryPolicy
from app.core.ml_model import BatchPolicy, ModelRegistry, load_registry
from app.core.observability.metrics import Metrics
from app.core.ports import LLMProvider
from app.core.providers import build_provider
from app.core.rate_limiter import (
    FallbackRateLimiter,
    InMemoryRateLimiter,
    RateLimiter,
    RateLimitPolicy,
    RedisRateLimiter,
)
from app.core.redis_gateway import RedisGateway
from app.services.classification import ClassificationService
from app.services.inference import CoalescingPolicy, InferenceService
from app.services.streaming import StreamingPolicy, StreamingService


@dataclass
class Lifecycle:
    """Estado de ciclo de vida de la réplica, consultado por `/health/ready`."""

    ready: bool = False
    shutting_down: bool = False


@dataclass
class Container:
    settings: Settings
    clock: Clock
    metrics: Metrics
    redis: RedisGateway
    key_store: KeyStore
    rate_limiter: RateLimiter
    llm: ResilientLLMClient
    inference: InferenceService
    streaming: StreamingService
    audit: QueuedAuditSink
    classification: ClassificationService
    lifecycle: Lifecycle = field(default_factory=Lifecycle)

    @classmethod
    async def build(
        cls,
        settings: Settings,
        *,
        metrics: Metrics | None = None,
        clock: Clock | None = None,
        redis_client: Redis | None = None,
        provider: LLMProvider | None = None,
        registry: ModelRegistry | None = None,
    ) -> Container:
        clock = clock or SystemClock()
        metrics = metrics or Metrics()

        redis = (
            RedisGateway(
                redis_client,
                clock,
                settings.redis_failure_threshold,
                settings.redis_cooldown_s,
            )
            if redis_client is not None
            else RedisGateway.from_url(
                settings.redis_url,
                clock,
                socket_timeout_s=settings.redis_socket_timeout_s,
                max_connections=settings.redis_max_connections,
                failure_threshold=settings.redis_failure_threshold,
                cooldown_s=settings.redis_cooldown_s,
            )
        )

        def fallback_counter(component: str) -> Callable[[], None]:
            def increment() -> None:
                metrics.redis_fallback.labels(component).inc()

            return increment

        rate_policy = RateLimitPolicy(settings.rate_limit_requests, settings.rate_limit_window_s)
        rate_limiter = FallbackRateLimiter(
            RedisRateLimiter(redis, rate_policy, clock),
            InMemoryRateLimiter(rate_policy, clock),
            on_fallback=fallback_counter("rate_limit"),
        )

        breaker_policy = BreakerPolicy(
            failure_threshold=settings.cb_failure_threshold,
            window_s=settings.cb_window_s,
            open_s=settings.cb_open_s,
            probe_ttl_s=settings.llm_default_timeout_s,
        )
        breaker = CircuitBreaker(
            FallbackBreakerStore(
                RedisBreakerStore(redis, breaker_policy),
                InMemoryBreakerStore(breaker_policy),
            ),
            clock,
        )
        llm = ResilientLLMClient(
            provider or build_provider(settings),
            breaker,
            clock,
            metrics,
            max_concurrency=settings.llm_max_concurrency,
            queue_timeout_s=settings.llm_queue_timeout_s,
            retry=RetryPolicy(
                max_retries=settings.llm_max_retries,
                base_delay_s=settings.llm_retry_base_delay_s,
            ),
        )
        inference = InferenceService(
            llm,
            RedisCache(redis, on_fallback=fallback_counter("cache")),
            RedisLock(redis),
            clock,
            metrics,
            CoalescingPolicy(
                cache_ttl_s=settings.cache_ttl_s,
                lock_ttl_s=settings.single_flight_lock_ttl_s,
                max_wait_s=settings.single_flight_wait_s,
            ),
        )
        audit = QueuedAuditSink(
            RedisStreamAuditWriter(redis, maxlen=settings.audit_stream_maxlen),
            metrics,
            queue_size=settings.audit_queue_size,
        )
        streaming = StreamingService(
            llm,
            audit,
            clock,
            metrics,
            StreamingPolicy(
                buffer_size=settings.stream_buffer_size,
                slow_consumer_timeout_s=settings.stream_slow_consumer_timeout_s,
            ),
        )
        # R3-AC01/02: los modelos se cargan una sola vez, acá (dentro del lifespan) y fuera del
        # event loop. Si faltan o no pasan el checksum, la réplica no arranca (fail fast).
        if registry is None:
            registry = await asyncio.to_thread(load_registry, settings.ml_models_dir)
        classification = ClassificationService(
            registry,
            clock,
            metrics,
            default_version=settings.ml_default_version,
            max_threads=settings.ml_max_threads,
            batching=BatchPolicy(
                window_s=settings.ml_batch_window_ms / 1000,
                max_batch_size=settings.ml_max_batch_size,
            )
            if settings.ml_batching_enabled
            else None,
        )
        return cls(
            settings=settings,
            clock=clock,
            metrics=metrics,
            redis=redis,
            key_store=StaticKeyStore(parse_api_keys(settings.api_keys.get_secret_value())),
            rate_limiter=rate_limiter,
            llm=llm,
            inference=inference,
            streaming=streaming,
            audit=audit,
            classification=classification,
        )

    async def start(self) -> None:
        self.audit.start()
        self.classification.start()
        self.lifecycle.ready = True

    async def aclose(self) -> None:
        self.lifecycle.shutting_down = True
        self.lifecycle.ready = False
        await self.classification.aclose()
        await self.audit.aclose()
        await self.llm.aclose()
        await self.redis.aclose()
