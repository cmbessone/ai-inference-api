"""Matriz de trazabilidad SDD: criterio de aceptación → tests que lo verifican.

Lee los criterios de `specs/**/spec.md` y busca sus IDs en los docstrings de `tests/`.
Actualiza la tabla de `specs/README.md` y falla (exit 1) si algún criterio no tiene test.

Uso: `make trace`
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRITERION = re.compile(r"\*\*([A-Z0-9]+-AC\d{2})\*\*")
# Menciones en tests: "R1-AC04", "RS-AC01/02" (lista) o "R3-AC03..05" (rango).
MENTION = re.compile(r"([A-Z0-9]+)-AC(\d{2})((?:/\d{2})*)(?:\.\.(\d{2}))?")
TEST_DEF = re.compile(r"^(?:async )?def (test_\w+)", re.MULTILINE)
START, END = "<!-- trace:start -->", "<!-- trace:end -->"


def criteria() -> list[str]:
    found: list[str] = []
    for spec in sorted((ROOT / "specs").glob("*/spec.md")):
        found += CRITERION.findall(spec.read_text())
    return found


def expand(prefix: str, first: str, extra: str, until: str | None) -> list[str]:
    numbers = [int(first), *(int(n) for n in extra.split("/") if n)]
    if until:
        numbers = list(range(int(first), int(until) + 1))
    return [f"{prefix}-AC{n:02d}" for n in numbers]


def coverage() -> dict[str, list[str]]:
    covered: dict[str, list[str]] = defaultdict(list)
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        source = path.read_text()
        defs = [(m.start(), m.group(1)) for m in TEST_DEF.finditer(source)]
        for mention in MENTION.finditer(source):
            owner = next((name for pos, name in reversed(defs) if pos < mention.start()), None)
            if owner is None:
                continue
            for criterion in expand(*mention.groups()):
                label = f"`{path.stem}::{owner}`"
                if label not in covered[criterion]:
                    covered[criterion].append(label)
    return covered


def render(all_criteria: list[str], covered: dict[str, list[str]]) -> str:
    rows = ["| Criterio | Tests |", "|---|---|"]
    for criterion in all_criteria:
        tests = covered.get(criterion)
        rows.append(f"| {criterion} | {'<br>'.join(tests) if tests else '❌ sin test'} |")
    return "\n".join(rows)


def main() -> int:
    all_criteria = criteria()
    covered = coverage()
    missing = [c for c in all_criteria if c not in covered]
    readme = ROOT / "specs" / "README.md"
    text = readme.read_text()
    table = f"{START}\n{render(all_criteria, covered)}\n{END}"
    if START in text:
        text = re.sub(f"{START}.*{END}", table, text, flags=re.DOTALL)
    else:
        text += f"\n## Trazabilidad (generada con `make trace`)\n\n{table}\n"
    readme.write_text(text)
    total = len(all_criteria)
    print(f"{total - len(missing)}/{total} criterios con test")
    if missing:
        print("Sin test: " + ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
