import ast
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import megooflags_api.evaluation as evaluation
from megooflags_api.evaluation.bucketing import BUCKETS, bucket, bucket_text, pick_variation
from megooflags_api.evaluation.model import Weight


def test_bucket_known_value() -> None:
    # printf 'f.u' | sha256sum -> fbf3509bcdd6ea42...; 0xFBF3509BCDD6EA42 % 100000
    assert bucket("f", "u") == 68258


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("abc", "abc"),
        (42, "42"),
        (42.0, "42"),
        (-0.0, "0"),
        (2**53 - 1, "9007199254740991"),
        (2**53, None),
        (1.5, None),
        (True, None),
        (None, None),
        ([1], None),
    ],
)
def test_bucket_text(value: Any, expected: str | None) -> None:
    assert bucket_text(value) == expected


def test_pick_variation_boundaries() -> None:
    weights = [Weight("a", 30000), Weight("b", 70000)]
    assert pick_variation(weights, 29999) == "a"
    assert pick_variation(weights, 30000) == "b"
    assert pick_variation([Weight("z", 0), Weight("a", 100000)], 0) == "a"


def test_distribution() -> None:
    weights = [Weight("a", 25000), Weight("b", 25000), Weight("c", 50000)]
    n = 100_000
    counts = Counter(pick_variation(weights, bucket("rollout", f"user-{i}")) for i in range(n))
    for w in weights:
        expected = n * w.weight // BUCKETS
        assert abs(counts[w.variation] - expected) <= 1000, (w.variation, counts)


def _import_roots(tree: ast.AST) -> list[str | None]:
    roots: list[str | None] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.append(None if node.level else node.module)
    return roots


def test_evaluation_is_stdlib_only() -> None:
    package = Path(evaluation.__file__).parent
    files = sorted(package.glob("*.py"))
    assert files
    for path in files:
        for name in _import_roots(ast.parse(path.read_text(), str(path))):
            if name is None:
                continue  # relative import
            ok = (
                name.split(".")[0] in sys.stdlib_module_names
                or name == "megooflags_api.evaluation"
                or name.startswith("megooflags_api.evaluation.")
            )
            assert ok, f"{path.name} imports {name}"
