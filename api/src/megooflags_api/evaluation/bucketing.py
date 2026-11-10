"""Sticky percentage bucketing. Every SDK must reproduce it bit for bit."""

import hashlib
from collections.abc import Sequence
from typing import Any

from megooflags_api.evaluation.model import Weight

BUCKETS = 100_000
MAX_SAFE_INTEGER = 2**53 - 1


def bucket_text(value: Any) -> str | None:
    """The text hashed for `value`, or None when it can't be bucketed (counts as missing)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) <= MAX_SAFE_INTEGER else None
    if isinstance(value, float) and value.is_integer() and abs(value) <= MAX_SAFE_INTEGER:
        return str(int(value))
    return None


def bucket(flag_key: str, text: str) -> int:
    """sha256(utf8('<flag_key>.<text>'))[0:8] as an unsigned big-endian integer, mod 100000."""
    digest = hashlib.sha256(f"{flag_key}.{text}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def pick_variation(weights: Sequence[Weight], value: int) -> str:
    """The first variation whose running total of weights is greater than `value`."""
    running = 0
    for w in weights:
        running += w.weight
        if running > value:
            return w.variation
    raise ValueError("weights do not add up to 100000")  # unreachable after parse_ruleset
