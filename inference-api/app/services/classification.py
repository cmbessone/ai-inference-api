"""Caso de uso: clasificación con el modelo propio (R3)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from anyio import CapacityLimiter, to_thread

from app.core.clock import Clock
from app.core.ml_model import (
    BatchPolicy,
    MicroBatcher,
    ModelRegistry,
    Prediction,
    resolve_version,
)
from app.core.observability.metrics import Metrics


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    prediction: Prediction
    version: str
    latency_ms: int


class ClassificationService:
    def __init__(
        self,
        registry: ModelRegistry,
        clock: Clock,
        metrics: Metrics,
        *,
        default_version: str,
        max_threads: int,
        batching: BatchPolicy | None,
    ) -> None:
        self._registry = registry
        self._clock = clock
        self._default_version = default_version
        # Thread pool propio y acotado: la inferencia CPU-bound no compite con el pool por
        # defecto de anyio (que usan otras librerías) ni puede crecer sin límite (R3-AC06).
        self._limiter = CapacityLimiter(max_threads)
        self._batchers = (
            {
                version: MicroBatcher(registry.get(version), self._run_in_thread, batching, metrics)
                for version in registry.versions()
            }
            if batching is not None
            else {}
        )

    @property
    def versions(self) -> list[str]:
        return self._registry.versions()

    async def _run_in_thread(
        self, fn: Callable[[Sequence[str]], list[Prediction]], texts: Sequence[str]
    ) -> list[Prediction]:
        return await to_thread.run_sync(fn, texts, limiter=self._limiter)

    async def classify(self, text: str, requested_version: str | None) -> ClassificationResult:
        version = resolve_version(requested_version, self.versions, self._default_version)
        started = self._clock.monotonic()
        if batcher := self._batchers.get(version):
            prediction = await batcher.submit(text)
        else:
            [prediction] = await self._run_in_thread(
                self._registry.get(version).predict_batch, [text]
            )
        latency_ms = round((self._clock.monotonic() - started) * 1000)
        return ClassificationResult(prediction, version, latency_ms)

    async def warmup(self) -> None:
        """Una inferencia por versión antes de declararse *ready*.

        La primera predicción paga imports perezosos de sklearn/numpy y el arranque del thread
        pool (~0.5 s medido): mejor pagarlo en el arranque que en el P95 de un cliente.
        """
        for version in self.versions:
            await self._run_in_thread(self._registry.get(version).predict_batch, ["warmup"])

    def start(self) -> None:
        for batcher in self._batchers.values():
            batcher.start()

    async def aclose(self) -> None:
        for batcher in self._batchers.values():
            await batcher.aclose()
