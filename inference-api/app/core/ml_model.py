"""Modelo ML propio: carga única verificada, registry de versiones y micro-batching (R3).

- **Carga** (R3-AC01/02/08): `load_registry` es síncrona y se ejecuta una sola vez en el
  `lifespan` vía `asyncio.to_thread`, verificando el SHA-256 de cada artefacto contra el manifest
  antes de deserializarlo (joblib usa pickle: nunca cargar artefactos no verificados).
- **Registry** (R3-AC03/04/05): mapa inmutable versión → clasificador; la resolución de la
  versión pedida es una función pura.
- **Inferencia** (R3-AC06): siempre fuera del event loop, en un thread pool acotado.
- **Micro-batching** (R3-AC09): acumula requests concurrentes durante una ventana corta y los
  infiere en una sola llamada vectorizada.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import joblib

from app.core.errors import UnknownModelVersionError
from app.core.observability.logging import get_logger
from app.core.observability.metrics import Metrics

logger = get_logger("ml")

MANIFEST = "manifest.json"


class ModelIntegrityError(RuntimeError):
    """El artefacto no coincide con su checksum: no se carga."""


@dataclass(frozen=True, slots=True)
class Prediction:
    label: str
    confidence: float


class TextClassifier(Protocol):
    version: str

    def predict_batch(self, texts: Sequence[str]) -> list[Prediction]: ...


# ── Funciones puras ─────────────────────────────────────────────────────────────


def to_predictions(
    probabilities: Iterable[Sequence[float]], classes: Sequence[str]
) -> list[Prediction]:
    """Fila de probabilidades → (clase más probable, su probabilidad)."""
    predictions = []
    for row in probabilities:
        best = max(range(len(row)), key=row.__getitem__)
        predictions.append(
            Prediction(label=str(classes[best]), confidence=round(float(row[best]), 4))
        )
    return predictions


def resolve_version(requested: str | None, available: Iterable[str], default: str) -> str:
    versions = sorted(available)
    version = (requested or default).strip().lower()
    if version not in versions:
        raise UnknownModelVersionError(
            f"Versión '{version}' no existe. Disponibles: {', '.join(versions)}"
        )
    return version


# ── Adaptador scikit-learn + carga ──────────────────────────────────────────────


class SklearnClassifier:
    def __init__(self, version: str, pipeline: Any) -> None:
        self.version = version
        self._pipeline = pipeline
        self._classes: Sequence[str] = list(pipeline.classes_)

    def predict_batch(self, texts: Sequence[str]) -> list[Prediction]:
        return to_predictions(self._pipeline.predict_proba(list(texts)), self._classes)


def verify_checksum(path: Path, expected: str) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ModelIntegrityError(f"{path.name}: sha256 {actual} != manifest {expected}")


def load_registry(models_dir: str | Path) -> ModelRegistry:
    """Bloqueante (I/O + deserialización): llamar con `asyncio.to_thread`."""
    root = Path(models_dir)
    manifest_path = root / MANIFEST
    if not manifest_path.exists():
        raise FileNotFoundError(f"No hay manifest en {root}. Ejecutá `make models`.")
    manifest = json.loads(manifest_path.read_text())
    classifiers: dict[str, TextClassifier] = {}
    for version, meta in manifest["versions"].items():
        artifact = root / meta["file"]
        verify_checksum(artifact, meta["sha256"])
        classifiers[version] = SklearnClassifier(version, joblib.load(artifact))
        logger.info("model_loaded", version=version, algorithm=meta.get("algorithm"))
    return ModelRegistry(classifiers)


class ModelRegistry:
    def __init__(self, classifiers: Mapping[str, TextClassifier]) -> None:
        self._classifiers = MappingProxyType(dict(classifiers))

    def versions(self) -> list[str]:
        return sorted(self._classifiers)

    def get(self, version: str) -> TextClassifier:
        return self._classifiers[version]


# ── Micro-batching ──────────────────────────────────────────────────────────────

RunInThread = Callable[
    [Callable[[Sequence[str]], list[Prediction]], Sequence[str]], Awaitable[list[Prediction]]
]


@dataclass(frozen=True, slots=True)
class BatchPolicy:
    window_s: float = 0.010
    max_batch_size: int = 64


class MicroBatcher:
    """Agrupa requests concurrentes de una versión en una sola llamada vectorizada.

    El worker toma el primer pedido, espera hasta `window_s` (o `max_batch_size`) y despacha el
    batch al thread pool como tarea independiente: mientras un batch se infiere, el siguiente ya
    se está acumulando (pipelining). La concurrencia real la acota el limiter del thread pool.
    """

    def __init__(
        self,
        classifier: TextClassifier,
        run_in_thread: RunInThread,
        policy: BatchPolicy,
        metrics: Metrics,
    ) -> None:
        self._classifier = classifier
        self._run_in_thread = run_in_thread
        self._policy = policy
        self._metrics = metrics
        self._queue: asyncio.Queue[tuple[str, asyncio.Future[Prediction]]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._inflight: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(
                self._run(), name=f"batcher-{self._classifier.version}"
            )

    async def submit(self, text: str) -> Prediction:
        future: asyncio.Future[Prediction] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((text, future))
        return await future

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            batch = [await self._queue.get()]
            deadline = loop.time() + self._policy.window_s
            while len(batch) < self._policy.max_batch_size:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout(remaining):
                        batch.append(await self._queue.get())
                except TimeoutError:
                    break
            task = asyncio.create_task(self._execute(batch))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _execute(self, batch: list[tuple[str, asyncio.Future[Prediction]]]) -> None:
        self._metrics.classify_batch_size.observe(len(batch))
        try:
            predictions = await self._run_in_thread(
                self._classifier.predict_batch, [text for text, _ in batch]
            )
        except Exception as exc:  # se propaga a cada request del batch
            for _, future in batch:
                if not future.done():
                    future.set_exception(exc)
            return
        for (_, future), prediction in zip(batch, predictions, strict=True):
            if not future.done():  # el cliente pudo haberse ido
                future.set_result(prediction)

    async def aclose(self) -> None:
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
