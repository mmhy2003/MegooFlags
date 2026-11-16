"""Exposure events and evaluation counts a worker keeps until the control plane accepts them.

New items wait in memory and are sealed into batch files under the buffer directory:
`counts-<ns>-<uuid>.json` and `exposures-<ns>-<uuid>.json`, each one `ClusterEventsIn` of at most
`MAX_BATCH` items, named after its batch id. Once a file has been offered to the control plane its
content and batch id never change, so a batch sent again after an ambiguous failure is stored once.
The files survive a restart; files older than 7 days (the control plane's batch retention) are
dropped when they are loaded.

Count files this process wrote and never offered are rewritten in place as new counts arrive, so
an outage grows them with the number of flags, variants and hours, not with time. The files are
bounded by `max_bytes`: past it, the oldest exposure files are deleted, except the one being sent.
Count files are never dropped for size.

Sealing writes and syncs files, so it runs in a worker thread (`run_sealer`, `BufferEventSink`).
The memory queues and the file index each have a short lock; file operations are serialised by a
third, which the event loop never waits on.
"""

import asyncio
import logging
import re
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from megooflags_api.schemas.cluster import ClusterEventsIn
from megooflags_api.schemas.events import MAX_BATCH, CountIn, ExposureIn
from megooflags_api.services.events import CountKey, truncate_hour
from megooflags_api.services.scheduler import now_utc
from megooflags_api.services.worker.store import TMP_SUFFIX, write_atomic

logger = logging.getLogger(__name__)

SendResult = Literal["accepted", "rejected", "retry"]
Kind = Literal["counts", "exposures"]

MAX_AGE = timedelta(days=7)
MAX_COUNT = 1_000_000_000  # the largest `CountIn.count`
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FILE_NAME = re.compile(r"^(counts|exposures)-(\d{20})-([0-9a-f-]{36})\.json$")


@dataclass(slots=True, frozen=True)
class _File:
    kind: Kind
    ns: int
    items: int
    size: int


@dataclass(slots=True)
class _CountTarget:
    """A count file being filled by `seal`: an unsent one rewritten in place, or a new one."""

    environment_id: uuid.UUID
    name: str | None = None
    counts: dict[CountKey, int] = field(default_factory=dict)
    added: dict[CountKey, int] = field(default_factory=dict)
    items: int = 0

    def fits(self, key: CountKey, n: int) -> bool:
        old = self.counts.get(key, 0)
        return self.items - _items(old) + _items(old + n) <= MAX_BATCH

    def add(self, key: CountKey, n: int) -> None:
        old = self.counts.get(key, 0)
        self.items += _items(old + n) - _items(old)
        self.counts[key] = old + n
        self.added[key] = self.added.get(key, 0) + n


def _ns(value: datetime) -> int:
    return (value - EPOCH) // timedelta(microseconds=1) * 1000


def _items(n: int) -> int:
    """The `CountIn` entries a total of `n` needs."""
    return -(-n // MAX_COUNT)


def _count_items(counts: Mapping[CountKey, int]) -> list[CountIn]:
    """One entry per key, split where a total is above `MAX_COUNT`."""
    items: list[CountIn] = []
    for (_, flag_key, variant, hour), total in counts.items():
        while total > 0:
            n = min(total, MAX_COUNT)
            items.append(CountIn(flag_key=flag_key, variant=variant or None, hour=hour, count=n))
            total -= n
    return items


def _sum_counts(environment_id: uuid.UUID, items: Iterable[CountIn]) -> dict[CountKey, int]:
    counts: dict[CountKey, int] = {}
    for item in items:
        key = (environment_id, item.flag_key, item.variant or "", truncate_hour(item.hour))
        counts[key] = counts.get(key, 0) + item.count
    return counts


class EventBuffer:
    seal_seconds: float = 10.0
    forward_seconds: float = 2.0

    def __init__(
        self,
        directory: Path,
        max_bytes: int,
        memory_items: int = 10_000,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._directory = directory
        self._max_bytes = max_bytes
        self._memory_items = memory_items
        self._clock = clock
        self._memory_lock = threading.Lock()
        self._counts: dict[CountKey, int] = {}
        self._exposures: dict[uuid.UUID, list[ExposureIn]] = {}
        self._exposure_items = 0
        self._io_lock = threading.Lock()
        self._index_lock = threading.Lock()
        self._files: dict[str, _File] = {}
        self._file_items = 0
        # Count files written by this process and never offered: name -> (environment, counts).
        self._unsent_counts: dict[str, tuple[uuid.UUID, dict[CountKey, int]]] = {}
        self._sending: str | None = None
        self._last_ns = 0
        self.dropped_total = 0

    @property
    def pending_events(self) -> int:
        """Exposures plus count entries in memory and in files."""
        return self._exposure_items + len(self._counts) + self._file_items

    def load(self) -> None:
        """Pick up the batch files of an earlier run; drop files older than 7 days or unreadable.

        They may have been offered before the restart, so they are never rewritten.
        """
        self._directory.mkdir(parents=True, exist_ok=True)
        cutoff = _ns(self._clock() - MAX_AGE)
        with self._io_lock:
            for path in self._directory.iterdir():
                if path.name.endswith(TMP_SUFFIX):
                    path.unlink(missing_ok=True)
                    continue
                match = FILE_NAME.match(path.name)
                try:
                    if match is None:
                        raise ValueError("not a batch file name")
                    batch = ClusterEventsIn.model_validate_json(path.read_bytes())
                except (OSError, ValueError) as exc:
                    logger.warning("deleting unreadable event batch %s: %s", path.name, exc)
                    path.unlink(missing_ok=True)
                    continue
                kind: Kind = "counts" if match.group(1) == "counts" else "exposures"
                ns = int(match.group(2))
                items = len(batch.exposures) + len(batch.counts)
                if ns < cutoff:
                    logger.warning("dropping event batch %s: older than 7 days", path.name)
                    path.unlink(missing_ok=True)
                    self.dropped_total += items
                    continue
                self._index(path.name, _File(kind, ns, items, path.stat().st_size))
                self._last_ns = max(self._last_ns, ns)
            self._enforce_max_bytes()

    def add_counts(
        self,
        environment_id: uuid.UUID,
        results: Iterable[tuple[str, str | None]],
        hour: datetime | None = None,
    ) -> None:
        """Count one evaluation for each flag key and variant, in the hour of `hour` or now."""
        bucket = truncate_hour(hour or self._clock())
        with self._memory_lock:
            for flag_key, variant in results:
                key = (environment_id, flag_key, variant or "", bucket)
                self._counts[key] = self._counts.get(key, 0) + 1

    def add_count_items(self, environment_id: uuid.UUID, counts: Sequence[CountIn]) -> None:
        self._merge_counts(_sum_counts(environment_id, counts))

    def add_exposures(
        self, environment_id: uuid.UUID, items: Sequence[ExposureIn], *, persist_context: bool
    ) -> None:
        """Queue exposures, without their context unless `persist_context`; seal past the limit."""
        if not persist_context:
            items = [item.model_copy(update={"context": None}) for item in items]
        with self._memory_lock:
            self._exposures.setdefault(environment_id, []).extend(items)
            self._exposure_items += len(items)
            over = self._exposure_items > self._memory_items
        if over:
            self.seal()

    def seal(self) -> None:
        """Write the items in memory to batch files of at most `MAX_BATCH` items each.

        Each file is written on its own. When one fails, its counts go back to memory and its
        exposures are dropped (and counted), so memory stays bounded and the others are kept.
        """
        with self._io_lock:
            with self._memory_lock:
                counts, self._counts = self._counts, {}
                exposures, self._exposures = self._exposures, {}
                self._exposure_items = 0
            self._seal_counts(counts)
            self._seal_exposures(exposures)
            self._enforce_max_bytes()

    async def forward(self, send: Callable[[ClusterEventsIn], Awaitable[SendResult]]) -> None:
        """One round: send the batch files oldest first, until one has to be retried."""
        last_ns = self._last_ns
        while (taken := await asyncio.to_thread(self._take_oldest, last_ns)) is not None:
            name, batch = taken
            try:
                try:
                    result = await send(batch)
                except Exception:
                    logger.exception("sending event batch %s failed; retrying later", name)
                    result = "retry"
                if result == "rejected":
                    logger.warning("the control plane rejected event batch %s; deleting it", name)
                if result != "retry":
                    await asyncio.to_thread(self._delete_locked, name)
            finally:
                self._sending = None  # only now may it be dropped for size
            if result == "retry":
                return

    async def run_sealer(self) -> None:
        while True:
            await asyncio.sleep(self.seal_seconds)
            try:
                await asyncio.to_thread(self.seal)
            except Exception:
                logger.exception("sealing buffered events failed; retrying later")

    async def run_forwarder(self, send: Callable[[ClusterEventsIn], Awaitable[SendResult]]) -> None:
        while True:
            await asyncio.sleep(self.forward_seconds)
            try:
                await self.forward(send)
            except Exception:
                logger.exception("forwarding buffered events failed; retrying later")

    def _merge_counts(self, counts: Mapping[CountKey, int]) -> None:
        with self._memory_lock:
            for key, n in counts.items():
                self._counts[key] = self._counts.get(key, 0) + n

    def _seal_counts(self, counts: Mapping[CountKey, int]) -> None:
        by_environment: dict[uuid.UUID, dict[CountKey, int]] = {}
        for key, n in counts.items():
            by_environment.setdefault(key[0], {})[key] = n
        for environment_id, pending in by_environment.items():
            targets = [
                _CountTarget(environment_id, name, dict(held), items=len(_count_items(held)))
                for name, (env, held) in self._unsent_counts.items()
                if env == environment_id
            ]
            for key, n in pending.items():
                target = next((t for t in targets if key in t.counts and t.fits(key, n)), None)
                target = target or next((t for t in targets if t.fits(key, n)), None)
                if target is None:
                    target = _CountTarget(environment_id)
                    targets.append(target)
                target.add(key, n)
            for target in targets:
                if target.added:
                    self._write_counts(target)

    def _write_counts(self, target: _CountTarget) -> None:
        if target.name is None:
            batch_id = uuid.uuid4()
        else:
            match = FILE_NAME.match(target.name)
            assert match is not None
            batch_id = uuid.UUID(match.group(3))
        batch = ClusterEventsIn(
            batch_id=batch_id,
            environment_id=target.environment_id,
            counts=_count_items(target.counts),
        )
        try:
            name = self._write("counts", batch, target.name)
        except Exception:
            logger.exception("writing buffered counts failed; keeping them in memory")
            self._merge_counts(target.added)
            return
        self._unsent_counts[name] = (target.environment_id, target.counts)

    def _seal_exposures(self, exposures: Mapping[uuid.UUID, list[ExposureIn]]) -> None:
        for environment_id, items in exposures.items():
            for start in range(0, len(items), MAX_BATCH):
                chunk = items[start : start + MAX_BATCH]
                batch = ClusterEventsIn(
                    batch_id=uuid.uuid4(), environment_id=environment_id, exposures=chunk
                )
                try:
                    self._write("exposures", batch)
                except Exception:
                    logger.exception("writing %d buffered exposures failed; dropping", len(chunk))
                    self.dropped_total += len(chunk)

    def _write(self, kind: Kind, batch: ClusterEventsIn, name: str | None = None) -> str:
        """Write `batch` to a new file, or replace the file `name`. Returns the file name."""
        if name is None:
            ns = max(_ns(self._clock()), self._last_ns + 1)
            name = f"{kind}-{ns:020d}-{batch.batch_id}.json"
        else:
            ns = self._files[name].ns
        data = batch.model_dump_json(exclude_defaults=True).encode()
        write_atomic(self._directory / name, data)
        self._last_ns = max(self._last_ns, ns)
        self._index(name, _File(kind, ns, len(batch.exposures) + len(batch.counts), len(data)))
        return name

    def _take_oldest(self, last_ns: int) -> tuple[str, ClusterEventsIn] | None:
        """The oldest file up to `last_ns`, marked as being sent and never to be rewritten."""
        with self._io_lock:
            for name in self._oldest_first():
                if self._files[name].ns > last_ns:
                    return None
                try:
                    batch = ClusterEventsIn.model_validate_json(
                        (self._directory / name).read_bytes()
                    )
                except (OSError, ValueError) as exc:
                    logger.warning("deleting unreadable event batch %s: %s", name, exc)
                    self._delete(name)
                    continue
                self._unsent_counts.pop(name, None)
                self._sending = name
                return name, batch
            return None

    def _oldest_first(self) -> list[str]:
        with self._index_lock:
            return sorted(self._files, key=lambda name: self._files[name].ns)

    def _index(self, name: str, file: _File) -> None:
        with self._index_lock:
            old = self._files.get(name)
            self._file_items += file.items - (old.items if old else 0)
            self._files[name] = file

    def _delete_locked(self, name: str) -> None:
        with self._io_lock:
            self._delete(name)

    def _delete(self, name: str) -> None:
        (self._directory / name).unlink(missing_ok=True)
        self._unsent_counts.pop(name, None)
        with self._index_lock:
            old = self._files.pop(name, None)
            if old is not None:
                self._file_items -= old.items

    def _enforce_max_bytes(self) -> None:
        with self._index_lock:
            total = sum(file.size for file in self._files.values())
        for name in self._oldest_first():
            if total <= self._max_bytes:
                return
            file = self._files[name]
            if file.kind != "exposures" or name == self._sending:
                continue
            logger.warning("event buffer full: dropping %d exposures in %s", file.items, name)
            self._delete(name)
            self.dropped_total += file.items
            total -= file.size
