"""R3 — carga, versionado, thread pool y micro-batching."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

import app.container as container_module
from app.config import Settings
from app.container import Container
from app.core.clock import SystemClock
from app.core.errors import UnknownModelVersionError
from app.core.ml_model import (
    BatchPolicy,
    ModelIntegrityError,
    ModelRegistry,
    Prediction,
    load_registry,
    resolve_version,
    to_predictions,
)
from app.core.observability.metrics import Metrics
from app.services.classification import ClassificationService
from tests.fakes import ScriptedProvider


def test_resolve_version_uses_header_or_default() -> None:
    """R3-AC03 + R3-AC04."""
    assert resolve_version("v1", ["v1", "v2"], default="v2") == "v1"
    assert resolve_version(" V1 ", ["v1", "v2"], default="v2") == "v1"
    assert resolve_version(None, ["v1", "v2"], default="v2") == "v2"


def test_unknown_version_lists_available() -> None:
    """R3-AC05."""
    with pytest.raises(UnknownModelVersionError, match=r"'v9' no existe\. Disponibles: v1, v2"):
        resolve_version("v9", ["v2", "v1"], default="v2")


def test_to_predictions_picks_argmax() -> None:
    preds = to_predictions([[0.1, 0.7, 0.2], [0.5, 0.25, 0.25]], ["neg", "neu", "pos"])
    assert preds == [Prediction("neu", 0.7), Prediction("neg", 0.5)]


def test_registry_loads_both_versions(models_dir: Path) -> None:
    """R3-AC01: v1 y v2 disponibles y con predicciones razonables."""
    registry = load_registry(models_dir)
    assert registry.versions() == ["v1", "v2"]
    [pred] = registry.get("v2").predict_batch(["excelente servicio, me encantó"])
    assert pred.label == "positive"
    assert 0 <= pred.confidence <= 1


def test_tampered_artifact_is_rejected(models_dir: Path, tmp_path: Path) -> None:
    """R3-AC08: un artefacto que no coincide con el manifest no se carga."""
    copy = tmp_path / "models"
    shutil.copytree(models_dir, copy)
    manifest = json.loads((copy / "manifest.json").read_text())
    manifest["versions"]["v1"]["sha256"] = "0" * 64
    (copy / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ModelIntegrityError):
        load_registry(copy)


async def test_loading_does_not_block_event_loop(
    settings: Settings, models_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3-AC02: mientras se cargan los modelos el event loop sigue atendiendo tareas."""

    def slow_load(path: str) -> ModelRegistry:
        time.sleep(0.2)  # carga pesada simulada (bloqueante)
        return load_registry(path)

    monkeypatch.setattr(container_module, "load_registry", slow_load)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    task = asyncio.create_task(ticker())
    container = await Container.build(settings, provider=ScriptedProvider())
    task.cancel()
    assert ticks >= 10  # con el loop bloqueado serían ~1
    assert container.classification.versions == ["v1", "v2"]


class EchoClassifier:
    """Clasificador falso: registra en qué hilo corre y cuántas veces es llamado."""

    version = "v1"

    def __init__(self) -> None:
        self.calls: list[int] = []
        self.threads: set[str] = set()

    def predict_batch(self, texts: Sequence[str]) -> list[Prediction]:
        self.calls.append(len(texts))
        self.threads.add(threading.current_thread().name)
        return [Prediction(label=text, confidence=1.0) for text in texts]


def make_service(classifier: EchoClassifier, batching: BatchPolicy | None) -> ClassificationService:
    return ClassificationService(
        ModelRegistry({"v1": classifier}),
        SystemClock(),
        Metrics(),
        default_version="v1",
        max_threads=2,
        batching=batching,
    )


async def test_inference_runs_in_worker_thread() -> None:
    """R3-AC06: la inferencia nunca corre en el hilo del event loop."""
    classifier = EchoClassifier()
    service = make_service(classifier, batching=None)
    result = await service.classify("hola", "v1")
    assert result.prediction.label == "hola"
    assert threading.main_thread().name not in classifier.threads


async def test_concurrent_requests_are_batched_and_routed_back() -> None:
    """R3-AC09: 50 requests concurrentes → pocas llamadas, cada uno recibe lo suyo."""
    classifier = EchoClassifier()
    service = make_service(classifier, batching=BatchPolicy(window_s=0.01, max_batch_size=64))
    service.start()
    try:
        texts = [f"texto-{i}" for i in range(50)]
        results = await asyncio.gather(*(service.classify(t, "v1") for t in texts))
    finally:
        await service.aclose()
    assert [r.prediction.label for r in results] == texts
    assert sum(classifier.calls) == 50
    assert len(classifier.calls) <= 3


async def test_batch_respects_max_size() -> None:
    classifier = EchoClassifier()
    service = make_service(classifier, batching=BatchPolicy(window_s=0.05, max_batch_size=8))
    service.start()
    try:
        await asyncio.gather(*(service.classify(str(i), "v1") for i in range(20)))
    finally:
        await service.aclose()
    assert max(classifier.calls) <= 8
