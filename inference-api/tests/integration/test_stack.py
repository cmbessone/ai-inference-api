"""Pruebas contra el stack real (`make up` / `make up-load`). Opt-in: `pytest -m integration`."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str]) -> str:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True)  # noqa: S603
    return result.stdout


def test_survives_replica_crash() -> None:
    """RS-AC14: matar una réplica bajo tráfico no corta el servicio."""
    output = run(["./scripts/chaos.sh"])
    assert "fallidos: 0" in output


def test_sustains_100_rps_under_sla() -> None:
    """OB-AC08: ~100 RPS sin errores y p95 < 200 ms (medido dentro de la red de Docker)."""
    output = run(["make", "load-internal"])
    assert "errores=0/" in output
    for p95 in re.findall(r"p95=([0-9.]+)ms", output):
        assert float(p95) < 200
