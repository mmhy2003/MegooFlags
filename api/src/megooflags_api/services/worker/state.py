"""What a worker knows about its link to the control plane, and whether it may serve."""

from collections.abc import Callable
from datetime import datetime, timedelta

from megooflags_api.services.scheduler import now_utc
from megooflags_api.services.worker.store import SnapshotStore

SAVE_EVERY = timedelta(seconds=10)


class WorkerState:
    def __init__(self, store: SnapshotStore, clock: Callable[[], datetime] = now_utc) -> None:
        self._store = store
        self._clock = clock
        self._saved_at: datetime | None = None
        self.connected = False
        self.draining = False
        self.incompatible: str | None = None

    def touch(self) -> None:
        """Note contact with the control plane; written to cluster.json at most every 10 s."""
        now = self._clock()
        self._store.meta.last_contact_at = now
        if self._saved_at is None or now - self._saved_at >= SAVE_EVERY:
            self._store.save_meta()
            self._saved_at = now

    def unavailable_reason(self) -> str | None:
        """Why SDK requests get 503: "revoked", "not_ready" without a valid snapshot, else None."""
        meta = self._store.meta
        if meta.revoked:
            return "revoked"
        if not (meta.synced and self._store.valid):
            return "not_ready"
        return None

    def readiness(self) -> str | None:
        """Why `/readyz` fails: the unavailable reason, else "draining"; None when ready."""
        return self.unavailable_reason() or ("draining" if self.draining else None)

    def snapshot_age(self) -> float | None:
        """Seconds since the control plane was last heard from; None if it never was."""
        contact = self._store.meta.last_contact_at
        if contact is None:
            return None
        return (self._clock() - contact).total_seconds()
