"""Exposure events, evaluation counts and their retention.

Exposures are stored one row each; the context only when persisting it is enabled. Evaluation
counts are summed per environment, flag, variant and hour and never store a context. Remote
evaluations are counted in memory and flushed periodically, so the hot path issues no statement.
"""

import asyncio
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.errors import payload_too_large
from megooflags_api.models import EvaluationCount, ExposureEvent
from megooflags_api.schemas.events import ExposureIn

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1_000_000
RETENTION_LOCK_ID = 7_808_001
CountKey = tuple[uuid.UUID, str, str, datetime]  # environment, flag key, variant or "", hour


def truncate_hour(value: datetime) -> datetime:
    """The UTC hour, so counts sent with different offsets share one bucket."""
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


async def limit_body_size(request: Request) -> None:
    if len(await request.body()) > MAX_BODY_BYTES:
        raise payload_too_large(f"the request body exceeds {MAX_BODY_BYTES} bytes")


async def record_exposures(
    session: AsyncSession,
    environment_id: uuid.UUID,
    items: Sequence[ExposureIn],
    *,
    persist_context: bool,
    now: datetime,
) -> None:
    """Insert all exposures in one statement; a future timestamp is clamped to `now`."""
    if not items:
        return
    rows = [
        {
            "environment_id": environment_id,
            "flag_key": item.flag_key,
            "variant": item.variant,
            "reason": item.reason,
            "occurred_at": min(item.timestamp, now),
            "context": item.context if persist_context else None,
        }
        for item in items
    ]
    await session.execute(insert(ExposureEvent).values(rows))


async def add_counts(session: AsyncSession, counts: Mapping[CountKey, int]) -> None:
    """Add to the stored counts. Keys must be unique: one statement can't update a row twice."""
    if not counts:
        return
    rows = [
        {"environment_id": env, "flag_key": flag, "variant": variant, "hour": hour, "count": n}
        for (env, flag, variant, hour), n in counts.items()
    ]
    stmt = insert(EvaluationCount).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["environment_id", "flag_key", "variant", "hour"],
        set_={"count": EvaluationCount.count + stmt.excluded.count},
    )
    await session.execute(stmt)


class EvaluationCounter:
    """Remote evaluations counted in memory, flushed to `evaluation_count`."""

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._clock = clock
        self._pending: dict[CountKey, int] = {}

    def record(self, environment_id: uuid.UUID, results: Iterable[tuple[str, str | None]]) -> None:
        hour = truncate_hour(self._clock())
        for flag_key, variant in results:
            key = (environment_id, flag_key, variant or "", hour)
            self._pending[key] = self._pending.get(key, 0) + 1

    async def flush(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        """Write the pending counts. Unless they were committed, they are kept for the next
        flush, also when the flush is cancelled at shutdown."""
        batch, self._pending = self._pending, {}
        if not batch:
            return
        committed = False
        try:
            async with sessionmaker() as session:
                await add_counts(session, batch)
                await session.commit()
                committed = True
        except Exception:
            logger.exception("flushing %d evaluation counts failed; retrying later", len(batch))
        finally:
            if not committed:
                for key, n in batch.items():
                    self._pending[key] = self._pending.get(key, 0) + n

    async def run(
        self, sessionmaker: async_sessionmaker[AsyncSession], interval: float = 10.0
    ) -> None:
        while True:
            await asyncio.sleep(interval)
            await self.flush(sessionmaker)


async def delete_expired(session: AsyncSession, retention_days: int) -> int:
    """Delete exposures received more than `retention_days` ago. Returns 0 without deleting
    while another replica holds the retention lock."""
    locked = await session.scalar(select(func.pg_try_advisory_xact_lock(RETENTION_LOCK_ID)))
    if not locked:
        return 0
    cutoff = func.now() - func.make_interval(0, 0, 0, retention_days)  # years, months, weeks, days
    result = await session.execute(delete(ExposureEvent).where(ExposureEvent.received_at < cutoff))
    return int(result.rowcount)  # type: ignore[attr-defined]


async def run_retention(
    sessionmaker: async_sessionmaker[AsyncSession],
    retention_days: int,
    interval: float = 3600.0,
    first_delay: float = 60.0,
) -> None:
    """Delete expired exposures every `interval`, starting `first_delay` after startup, so an
    idle replica issues no statement right after it starts."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            async with sessionmaker() as session:
                deleted = await delete_expired(session, retention_days)
                await session.commit()
            if deleted:
                logger.info("deleted %d expired exposure events", deleted)
        except Exception:
            logger.exception("deleting expired exposure events failed")
        await asyncio.sleep(interval)
