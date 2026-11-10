"""Latency statistics shared by the propagation test and the benchmark."""

import math
from collections.abc import Sequence


def percentile(samples: Sequence[float], q: float) -> float:
    """Nearest-rank percentile: the ceil(q * n)-th smallest sample."""
    if not samples:
        raise ValueError("percentile of no samples")
    ordered = sorted(samples)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]
