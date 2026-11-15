"""Hour-bucketed evaluation counts and exposures deduplicated per context key, flag, variant and
UTC hour. The Aggregator is not thread-safe: the batcher's thread alone owns it."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import islice
from typing import Any, NotRequired, TypedDict

# The largest count the server accepts in one count item.
MAX_COUNT = 1_000_000_000

_HOUR_MS = 3_600_000


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One flag evaluation as recorded by the provider. served is False when the default was
    returned (error, not ready); context_key is "" when the context key is not a string; context
    is only sent with send_context; at is Unix time in milliseconds."""

    flag_key: str
    variant: str
    served: bool
    reason: str
    context_key: str
    at: int
    context: dict[str, Any] | None = None


class Exposure(TypedDict):
    """That a context saw a variant."""

    flag_key: str
    variant: str
    reason: str
    timestamp: str
    context: NotRequired[dict[str, Any]]


class Count(TypedDict):
    """The evaluations of one flag and variant (None: not served) in one UTC hour."""

    flag_key: str
    variant: str | None
    hour: str
    count: int


@dataclass
class Batch:
    """The body of POST /sdk/v1/events."""

    exposures: list[Exposure] = field(default_factory=list)
    counts: list[Count] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.exposures) + len(self.counts)

    def body(self) -> bytes:
        """The JSON body, UTF-8. Raises TypeError or ValueError for a context that is not JSON."""
        doc = {"exposures": self.exposures, "counts": self.counts}
        return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


# (flag, variant or None when not served, hour as Unix hours).
_CountKey = tuple[str, str | None, int]


class Lru:
    """A fixed-size set that forgets the least recently seen key."""

    def __init__(self, capacity: int) -> None:
        self._cap = max(capacity, 1)
        self._items: OrderedDict[object, None] = OrderedDict()

    def __len__(self) -> int:
        return len(self._items)

    def seen(self, key: object) -> bool:
        """Whether key is present; marks it as the most recent. A new key is inserted, evicting
        the oldest one above the capacity."""
        if key in self._items:
            self._items.move_to_end(key)
            return True
        self._items[key] = None
        if len(self._items) > self._cap:
            self._items.popitem(last=False)
        return False


def _timestamp(at_ms: int) -> str:
    seconds, ms = divmod(at_ms, 1000)
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _hour(hour: int) -> str:
    return datetime.fromtimestamp(hour * 3600, UTC).strftime("%Y-%m-%dT%H:00:00Z")


def _parse_hour(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp()) // 3600


class Aggregator:
    """Buffers exposures and counts. An exposure beyond max_pending is dropped and counted in
    dropped; counts are never dropped."""

    def __init__(
        self, *, dedup_size: int = 10_000, max_pending: int = 10_000, send_context: bool = False
    ) -> None:
        self._dedup = Lru(dedup_size)
        self._max_pending = max_pending
        self._send_context = send_context
        # Buckets in the order they were first seen (dicts keep insertion order).
        self._counts: dict[_CountKey, int] = {}
        self._exposures: list[Exposure] = []
        self.dropped = 0

    def add(self, ev: Evaluation) -> None:
        """Count ev and, the first time a served variant is seen for its context key, flag and
        hour, queue an exposure."""
        hour = ev.at // _HOUR_MS
        key: _CountKey = (ev.flag_key, ev.variant if ev.served else None, hour)
        self._counts[key] = self._counts.get(key, 0) + 1

        if not ev.served or self._dedup.seen((ev.context_key, ev.flag_key, ev.variant, hour)):
            return
        if len(self._exposures) >= self._max_pending:
            self.dropped += 1
            return
        e: Exposure = {
            "flag_key": ev.flag_key,
            "variant": ev.variant,
            "reason": ev.reason,
            "timestamp": _timestamp(ev.at),
        }
        if self._send_context and ev.context is not None:
            e["context"] = ev.context
        self._exposures.append(e)

    def pending(self) -> tuple[int, int]:
        """The pending exposures and count buckets."""
        return len(self._exposures), len(self._counts)

    def take(self, limit: int) -> Batch:
        """Remove and return up to limit exposures (oldest first) and up to limit count items (in
        the order the buckets were first seen). A bucket above MAX_COUNT becomes several items;
        what does not fit stays for the next batch."""
        b = Batch(self._exposures[:limit], [])
        del self._exposures[:limit]
        for key in list(islice(self._counts, limit)):
            flag, variant, hour = key
            stamp = _hour(hour)
            left = self._counts[key]
            while left > 0 and len(b.counts) < limit:
                n = min(left, MAX_COUNT)
                left -= n
                b.counts.append({"flag_key": flag, "variant": variant, "hour": stamp, "count": n})
            if left > 0:
                self._counts[key] = left
                break
            del self._counts[key]
        return b

    def restore(self, b: Batch) -> None:
        """Put an unsent batch back in front of what is pending, so a retry sends it first. Its
        counts add to any newer count for the same bucket."""
        self._exposures[:0] = b.exposures
        counts: dict[_CountKey, int] = {}
        for c in b.counts:
            key: _CountKey = (c["flag_key"], c["variant"], _parse_hour(c["hour"]))
            counts[key] = counts.get(key, 0) + c["count"]
        for key, n in self._counts.items():
            counts[key] = counts.get(key, 0) + n
        self._counts = counts
