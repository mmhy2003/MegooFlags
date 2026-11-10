"""Strong ETags for SDK responses and `If-None-Match` matching."""

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """JSON with sorted keys and no whitespace, so equal values hash equally."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def derived_etag(*parts: str) -> str:
    """A strong ETag over `parts`; the NUL separator keeps ("ab", "c") apart from ("a", "bc")."""
    return '"' + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:32] + '"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Whether an `If-None-Match` header names `etag` (weak comparison) or is `*`."""
    if if_none_match is None:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate.removeprefix("W/") == etag:
            return True
    return False
