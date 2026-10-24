"""Entrena y serializa los modelos v1 y v2 con un manifest de integridad (R3-AC08).

Se ejecuta en el *build* de Docker (no en runtime), así cada imagen trae artefactos inmutables
y reproducibles. Uso: `python -m ml.train --out models`.

- v1: bolsa de palabras + Naive Bayes (rápido, baseline).
- v2: TF-IDF de palabras (1-2 gramas) y caracteres + regresión logística (más robusto a
  variaciones de escritura).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import FeatureUnion, Pipeline

from ml import dataset

MANIFEST = "manifest.json"


def build_v1() -> Pipeline:
    return Pipeline([("bow", CountVectorizer(lowercase=True)), ("clf", MultinomialNB(alpha=0.5))])


def build_v2() -> Pipeline:
    features = FeatureUnion(
        [
            ("words", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
            ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)),
        ]
    )
    return Pipeline(
        [("features", features), ("clf", LogisticRegression(C=10.0, max_iter=1000, random_state=7))]
    )


BUILDERS: dict[str, Callable[[], Pipeline]] = {"v1": build_v1, "v2": build_v2}
DESCRIPTIONS = {
    "v1": "CountVectorizer + MultinomialNB",
    "v2": "TF-IDF (palabras 1-2 + chars 3-5) + LogisticRegression",
}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def train_all(out_dir: Path) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    texts, labels = dataset.load()
    versions: dict[str, object] = {}
    for version, build in BUILDERS.items():
        model = build().fit(texts, labels)
        artifact = out_dir / f"{version}.joblib"
        joblib.dump(model, artifact)
        versions[version] = {
            "file": artifact.name,
            "sha256": sha256_of(artifact),
            "algorithm": DESCRIPTIONS[version],
            "labels": sorted(set(labels)),
        }
    manifest: dict[str, object] = {
        "trained_at": datetime.now(UTC).isoformat(),
        "versions": versions,
    }
    (out_dir / MANIFEST).write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("models"))
    args = parser.parse_args()
    manifest = train_all(args.out)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
