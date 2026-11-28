"""The current ruleset, filled from the bootstrap file, the disk cache, the SSE stream and the
poller."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal

from megooflags._engine import Ruleset

Source = Literal["bootstrap", "cache", "stream", "poll"]


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One parsed ruleset with the ETag and the source it came from."""

    ruleset: Ruleset
    etag: str
    source: Source


class Store:
    """Holds the current snapshot in one attribute. Readers take no lock: reading an attribute is
    atomic, and a snapshot is immutable, so a reader always sees a whole one. Writers serialise
    on a lock so that swap returns exactly the snapshot it replaced."""

    __slots__ = ("_current", "_lock")

    def __init__(self) -> None:
        self._current: Snapshot | None = None
        self._lock = threading.Lock()

    def load(self) -> Snapshot | None:
        """The current snapshot, or None before the first swap."""
        return self._current

    def swap(self, new: Snapshot) -> Snapshot | None:
        """Install new and return the snapshot it replaced."""
        with self._lock:
            old = self._current
            self._current = new
            return old
