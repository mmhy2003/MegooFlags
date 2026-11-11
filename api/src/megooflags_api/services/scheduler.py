"""One background scheduler per cluster. Each replica runs a `Scheduler`; the one holding the
session-level advisory lock on its dedicated connection is the leader and runs the jobs. When the
leader's connection drops, Postgres releases the lock and another replica takes over."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from megooflags_api import metrics

logger = logging.getLogger(__name__)

SCHEDULER_LOCK_ID = 7_808_002
MAX_PER_TICK = 100


class Publisher(Protocol):
    async def publish(self, project_id: uuid.UUID) -> None: ...


@dataclass
class JobResult:
    ok: int = 0
    failed: int = 0
    projects: set[uuid.UUID] = field(default_factory=set)


@dataclass(frozen=True)
class Job:
    name: str
    run: Callable[[async_sessionmaker[AsyncSession], datetime], Awaitable[JobResult]]
    every: timedelta | None = None  # None: every tick


def now_utc() -> datetime:
    return datetime.now(UTC)


def default_jobs() -> list[Job]:
    return []


class Scheduler:
    def __init__(
        self,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        bus: Publisher,
        *,
        interval: float = 5.0,
        jobs: list[Job] | None = None,
        clock: Callable[[], datetime] = now_utc,
        lock_id: int = SCHEDULER_LOCK_ID,
    ) -> None:
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.bus = bus
        self.interval = interval
        self.jobs = default_jobs() if jobs is None else jobs
        self.clock = clock
        self.lock_id = lock_id
        self.is_leader = False
        self.backend_pid: int | None = None
        self._conn: AsyncConnection | None = None
        self._last_run: dict[str, datetime] = {}

    def _set_leader(self, leader: bool) -> None:
        # inc/dec rather than set: several schedulers in one process (tests) share the gauge.
        if leader and not self.is_leader:
            metrics.SCHEDULER_LEADER.inc()
        elif not leader and self.is_leader:
            metrics.SCHEDULER_LEADER.dec()
        self.is_leader = leader

    async def ensure_leader(self) -> bool:
        """Take the lock if it is free, or check that the leader's connection is still alive."""
        try:
            if self._conn is None:
                self._conn = await self.engine.connect()
                got = await self._conn.scalar(
                    text("SELECT pg_try_advisory_lock(:id)"), {"id": self.lock_id}
                )
                self.backend_pid = await self._conn.scalar(text("SELECT pg_backend_pid()"))
                await self._conn.commit()
                if not got:
                    await self._conn.close()
                    self._conn = None
                    self.backend_pid = None
                self._set_leader(bool(got))
            else:
                await self._conn.execute(text("SELECT 1"))
                await self._conn.commit()
        except (DBAPIError, OSError, TimeoutError):
            logger.warning("scheduler connection lost; leadership released", exc_info=True)
            await self._drop_connection()
        return self.is_leader

    async def _drop_connection(self) -> None:
        conn, self._conn = self._conn, None
        self.backend_pid = None
        self._set_leader(False)
        if conn is not None:
            try:
                await conn.invalidate()
                await conn.close()
            except Exception:
                logger.debug("closing the lost scheduler connection failed", exc_info=True)

    async def tick(self) -> None:
        """Run every due job once and publish the projects they changed."""
        now = self.clock()
        projects: set[uuid.UUID] = set()
        for job in self.jobs:
            last = self._last_run.get(job.name)
            if job.every is not None and last is not None and now - last < job.every:
                continue
            self._last_run[job.name] = now
            try:
                result = await job.run(self.sessionmaker, now)
            except Exception:
                logger.exception("scheduler job %s failed", job.name)
                metrics.SCHEDULER_JOBS.labels(job=job.name, outcome="error").inc()
                continue
            metrics.SCHEDULER_JOBS.labels(job=job.name, outcome="ok").inc(result.ok)
            metrics.SCHEDULER_JOBS.labels(job=job.name, outcome="failed").inc(result.failed)
            projects |= result.projects
        for project_id in sorted(projects):
            await self.bus.publish(project_id)

    async def run(self) -> None:
        try:
            while True:
                try:
                    if await self.ensure_leader():
                        await self.tick()
                except Exception:
                    logger.exception("scheduler tick failed")
                await asyncio.sleep(self.interval)
        finally:
            await self.close()

    async def close(self) -> None:
        """Release the lock, so another replica takes over at once."""
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": self.lock_id})
                await conn.commit()
                await conn.close()
            except Exception:
                logger.debug("releasing the scheduler lock failed", exc_info=True)
                try:
                    await conn.invalidate()
                    await conn.close()
                except Exception:
                    logger.debug("closing the scheduler connection failed", exc_info=True)
        self.backend_pid = None
        self._set_leader(False)
