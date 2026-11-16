"""Exposure events and evaluation counts a worker keeps until the control plane accepts them.

New items wait in memory and are sealed into batch files under the buffer directory:
`counts-<ns>-<uuid>.json` and `exposures-<ns>-<uuid>.json`, each one `ClusterEventsIn` of at most
`MAX_BATCH` items. The batch id is fixed when the file is written, so a batch sent again after an
ambiguous failure is stored once by the control plane. The files survive a restart; files older
than 7 days (the control plane's batch retention) are dropped when they are loaded.

The files are bounded by `max_bytes`: past it, the oldest exposure files are deleted. Count files
are never dropped for size, as they are small and summed per flag, variant and hour.
"""

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
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
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FILE_NAME = re.compile(r"^(counts|exposures)-(\d{20})-([0-9a-f-]{36})\.json$")


@dataclass(slots=True, frozen=True)
class _File:
    kind: Kind
    ns: int
    items: int
    size: int


def _ns(value: datetime) -> int:
    return (value - EPOCH) // timedelta(microseconds=1) * 1000


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
        self._counts: dict[CountKey, int] = {}
        self._exposures: dict[uuid.UUID, list[ExposureIn]] = {}
        self._exposure_items = 0
        self._files: dict[str, _File] = {}
        self._last_ns = 0
        self.dropped_total = 0

    @property
    def pending_events(self) -> int:
        """Exposures plus count entries in memory and in files."""
        in_files = sum(file.items for file in self._files.values())
        return self._exposure_items + len(self._counts) + in_files

    def load(self) -> None:
        """Pick up the batch files of an earlier run; drop files older than 7 days or unreadable."""
        self._directory.mkdir(parents=True, exist_ok=True)
        cutoff = _ns(self._clock() - MAX_AGE)
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
            self._files[path.name] = _File(kind, ns, items, path.stat().st_size)
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
        for flag_key, variant in results:
            key = (environment_id, flag_key, variant or "", bucket)
            self._counts[key] = self._counts.get(key, 0) + 1

    def add_count_items(self, environment_id: uuid.UUID, counts: Sequence[CountIn]) -> None:
        for item in counts:
            key = (environment_id, item.flag_key, item.variant or "", truncate_hour(item.hour))
            self._counts[key] = self._counts.get(key, 0) + item.count

    def add_exposures(
        self, environment_id: uuid.UUID, items: Sequence[ExposureIn], *, persist_context: bool
    ) -> None:
        """Queue exposures, without their context unless `persist_context`; seal past the limit."""
        if not persist_context:
            items = [item.model_copy(update={"context": None}) for item in items]
        self._exposures.setdefault(environment_id, []).extend(items)
        self._exposure_items += len(items)
        if self._exposure_items > self._memory_items:
            self.seal()

    def seal(self) -> None:
        """Write the items in memory to batch files of at most `MAX_BATCH` items each.

        Items leave memory only once their file is written, so a failed write loses nothing.
        """
        self._seal_counts()
        self._seal_exposures()
        self._enforce_max_bytes()

    def _seal_counts(self) -> None:
        by_environment: dict[uuid.UUID, list[CountKey]] = {}
        for key in self._counts:
            by_environment.setdefault(key[0], []).append(key)
        for environment_id, keys in by_environment.items():
            for start in range(0, len(keys), MAX_BATCH):
                chunk = keys[start : start + MAX_BATCH]
                counts = [
                    CountIn(
                        flag_key=flag, variant=variant or None, hour=hour, count=self._counts[key]
                    )
                    for key in chunk
                    for (_, flag, variant, hour) in (key,)
                ]
                batch = ClusterEventsIn(
                    batch_id=uuid.uuid4(), environment_id=environment_id, counts=counts
                )
                self._write("counts", batch)
                for key in chunk:
                    del self._counts[key]

    def _seal_exposures(self) -> None:
        for environment_id, exposures in self._exposures.items():
            while exposures:
                items = exposures[:MAX_BATCH]
                batch = ClusterEventsIn(
                    batch_id=uuid.uuid4(), environment_id=environment_id, exposures=items
                )
                self._write("exposures", batch)
                del exposures[: len(items)]
                self._exposure_items -= len(items)
        self._exposures.clear()

    async def forward(self, send: Callable[[ClusterEventsIn], Awaitable[SendResult]]) -> None:
        """One round: send the batch files oldest first, until one has to be retried."""
        for name in self._oldest_first():
            path = self._directory / name
            try:
                batch = ClusterEventsIn.model_validate_json(path.read_bytes())
            except FileNotFoundError:
                self._files.pop(name, None)  # dropped for size while an earlier one was sent
                continue
            except (OSError, ValueError) as exc:
                logger.warning("deleting unreadable event batch %s: %s", name, exc)
                self._delete(name)
                continue
            try:
                result = await send(batch)
            except Exception:
                logger.exception("sending event batch %s failed; retrying later", name)
                return
            if result == "retry":
                return
            if result == "rejected":
                logger.warning("the control plane rejected event batch %s; deleting it", name)
            self._delete(name)

    async def run_sealer(self) -> None:
        while True:
            await asyncio.sleep(self.seal_seconds)
            try:
                self.seal()
            except Exception:
                logger.exception("sealing buffered events failed; retrying later")

    async def run_forwarder(self, send: Callable[[ClusterEventsIn], Awaitable[SendResult]]) -> None:
        while True:
            await asyncio.sleep(self.forward_seconds)
            await self.forward(send)

    def _write(self, kind: Kind, batch: ClusterEventsIn) -> None:
        ns = max(_ns(self._clock()), self._last_ns + 1)
        name = f"{kind}-{ns:020d}-{batch.batch_id}.json"
        data = batch.model_dump_json(exclude_defaults=True).encode()
        write_atomic(self._directory / name, data)
        self._last_ns = ns
        self._files[name] = _File(kind, ns, len(batch.exposures) + len(batch.counts), len(data))

    def _oldest_first(self) -> list[str]:
        return sorted(self._files, key=lambda name: self._files[name].ns)

    def _delete(self, name: str) -> None:
        (self._directory / name).unlink(missing_ok=True)
        self._files.pop(name, None)

    def _enforce_max_bytes(self) -> None:
        total = sum(file.size for file in self._files.values())
        for name in self._oldest_first():
            if total <= self._max_bytes:
                return
            file = self._files[name]
            if file.kind != "exposures":
                continue
            logger.warning("event buffer full: dropping %d exposures in %s", file.items, name)
            self._delete(name)
            self.dropped_total += file.items
            total -= file.size
