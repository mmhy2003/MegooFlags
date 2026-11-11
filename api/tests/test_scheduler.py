"""The leader-elected scheduler: one leader per database, failover, and how ticks run jobs."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from conftest import close_app
from helpers import FakeClock
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.db import create_engine, create_sessionmaker
from megooflags_api.main import background_jobs, create_app
from megooflags_api.services.scheduler import SCHEDULER_LOCK_ID, Job, JobResult, Scheduler
from megooflags_api.settings import Settings

TEST_LOCK_ID = SCHEDULER_LOCK_ID + 1000


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class RecordingBus:
    def __init__(self) -> None:
        self.published: list[uuid.UUID] = []

    async def publish(self, project_id: uuid.UUID) -> None:
        self.published.append(project_id)


@asynccontextmanager
async def schedulers(
    count: int, *, jobs: list[Job] | None = None, clock: FakeClock | None = None
) -> AsyncIterator[list[Scheduler]]:
    """`count` schedulers, each with its own engine, so each holds its own connection."""
    engines = [create_engine(Settings()) for _ in range(count)]
    made = [
        Scheduler(
            engine,
            create_sessionmaker(engine),
            RecordingBus(),
            jobs=jobs or [],
            clock=clock or FakeClock(),
            lock_id=TEST_LOCK_ID,
        )
        for engine in engines
    ]
    try:
        yield made
    finally:
        for scheduler in made:
            await scheduler.close()
        for engine in engines:
            await engine.dispose()


async def test_only_one_leader(migrated: None) -> None:
    before = sample("megooflags_scheduler_leader")
    async with schedulers(2) as (a, b):
        assert await a.ensure_leader() is True
        assert sample("megooflags_scheduler_leader") == before + 1
        assert await b.ensure_leader() is False
        assert await a.ensure_leader() is True
        assert a.is_leader and not b.is_leader
        assert sample("megooflags_scheduler_leader") == before + 1
    assert sample("megooflags_scheduler_leader") == before


async def test_failover_when_leader_connection_dies(migrated: None) -> None:
    async with schedulers(2) as (a, b):
        assert await a.ensure_leader() is True
        assert await b.ensure_leader() is False
        engine = create_engine(Settings())
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": a.backend_pid}
                )
                await conn.commit()
        finally:
            await engine.dispose()

        assert await a.ensure_leader() is False
        assert not a.is_leader
        assert await b.ensure_leader() is True


async def test_close_releases_the_lock(migrated: None) -> None:
    async with schedulers(2) as (a, b):
        assert await a.ensure_leader() is True
        await a.close()
        assert not a.is_leader
        assert await b.ensure_leader() is True


async def test_tick_runs_jobs_and_publishes(migrated: None) -> None:
    project = uuid.uuid4()
    ran: list[str] = []

    async def works(sessionmaker: async_sessionmaker[AsyncSession], now: datetime) -> JobResult:
        ran.append("works")
        return JobResult(ok=2, failed=1, projects={project})

    async def breaks(sessionmaker: async_sessionmaker[AsyncSession], now: datetime) -> JobResult:
        ran.append("breaks")
        raise RuntimeError("boom")

    async def after(sessionmaker: async_sessionmaker[AsyncSession], now: datetime) -> JobResult:
        ran.append("after")
        return JobResult()

    jobs = [Job("t_works", works), Job("t_breaks", breaks), Job("t_after", after)]
    counts = {
        (job, outcome): sample("megooflags_scheduler_jobs_total", job=job, outcome=outcome)
        for job in ("t_works", "t_breaks")
        for outcome in ("ok", "failed", "error")
    }
    async with schedulers(1, jobs=jobs) as (scheduler,):
        await scheduler.tick()
        assert scheduler.bus.published == [project]  # type: ignore[attr-defined]

    assert ran == ["works", "breaks", "after"]
    expected = {("t_works", "ok"): 2, ("t_works", "failed"): 1, ("t_breaks", "error"): 1}
    for (job, outcome), before in counts.items():
        after_count = sample("megooflags_scheduler_jobs_total", job=job, outcome=outcome)
        assert after_count == before + expected.get((job, outcome), 0), (job, outcome)


async def test_periodic_job_runs_at_most_every_interval(migrated: None) -> None:
    runs: list[datetime] = []

    async def sweep(sessionmaker: async_sessionmaker[AsyncSession], now: datetime) -> JobResult:
        runs.append(now)
        return JobResult()

    clock = FakeClock()
    async with schedulers(
        1, jobs=[Job("t_sweep", sweep, every=timedelta(minutes=15))], clock=clock
    ) as (scheduler,):
        await scheduler.tick()
        clock.advance(minutes=1)
        await scheduler.tick()
        clock.advance(minutes=15)
        await scheduler.tick()

    assert len(runs) == 2


async def test_background_jobs_include_scheduler() -> None:
    for enabled, expected in ((True, 1), (False, 0)):
        app = create_app(Settings(scheduler_enabled=enabled))
        try:
            jobs = background_jobs(app)
            names = [job.__qualname__ for job in jobs]
            for job in jobs:
                job.close()
        finally:
            await close_app(app)
        assert names.count("Scheduler.run") == expected
