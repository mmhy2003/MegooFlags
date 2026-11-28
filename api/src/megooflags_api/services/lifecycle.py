"""Flag lifecycle: the state a flag shows, the permanent mark and the stale sweep."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.models import Environment, EvaluationCount, Flag, Project
from megooflags_api.models.enums import FlagLifecycle
from megooflags_api.schemas.flags import LifecycleState
from megooflags_api.services.audit import record_system
from megooflags_api.services.scheduler import JobResult


def lifecycle_state(archived_at: datetime | None, lifecycle: FlagLifecycle) -> LifecycleState:
    """An archived flag shows "archived" whatever its stored state."""
    return "archived" if archived_at is not None else lifecycle.value


async def set_permanent(session: AsyncSession, flag: Flag, permanent: bool) -> None:
    """Marking sets permanent; clearing the mark gives active if the flag was ever evaluated,
    else new. Clearing a flag that is not permanent changes nothing."""
    if permanent:
        flag.lifecycle = FlagLifecycle.PERMANENT
    elif flag.lifecycle == FlagLifecycle.PERMANENT:
        flag.lifecycle = (
            FlagLifecycle.ACTIVE if flag.last_evaluated_at is not None else FlagLifecycle.NEW
        )
    await session.flush()


def next_state(
    lifecycle: FlagLifecycle,
    last_evaluated_at: datetime | None,
    created_at: datetime,
    cutoff: datetime,
) -> FlagLifecycle:
    """The state a live flag moves to; permanent flags keep theirs."""
    if lifecycle == FlagLifecycle.PERMANENT:
        return lifecycle
    reference = last_evaluated_at or created_at
    if reference < cutoff:
        return FlagLifecycle.STALE
    return FlagLifecycle.ACTIVE if last_evaluated_at is not None else FlagLifecycle.NEW


async def sweep(session: AsyncSession, now: datetime) -> tuple[int, set[uuid.UUID]]:
    """Store each live flag's last evaluation hour and move it between new, active and stale.

    `updated_at` is kept: the sweep is not an edit. Returns the number of state changes and the
    projects they were in.
    """
    last_hour = (
        select(Flag.id.label("flag_id"), func.max(EvaluationCount.hour).label("hour"))
        .join(Environment, Environment.project_id == Flag.project_id)
        .join(
            EvaluationCount,
            (EvaluationCount.environment_id == Environment.id)
            & (EvaluationCount.flag_key == Flag.key),
        )
        .where(Flag.archived_at.is_(None))
        .group_by(Flag.id)
        .subquery()
    )
    rows = await session.execute(
        select(
            Flag.id,
            Flag.project_id,
            Flag.key,
            Flag.lifecycle,
            Flag.last_evaluated_at,
            Flag.created_at,
            Project.stale_after_days,
            last_hour.c.hour,
        )
        .join(Project, Project.id == Flag.project_id)
        .outerjoin(last_hour, last_hour.c.flag_id == Flag.id)
        .where(Flag.archived_at.is_(None))
    )
    changes = 0
    changed: set[uuid.UUID] = set()
    for row in rows.all():
        # Counts past their retention are deleted; an older stored value is kept then.
        evaluated = max(
            (t for t in (row.last_evaluated_at, row.hour) if t is not None), default=None
        )
        cutoff = now - timedelta(days=row.stale_after_days)
        lifecycle = next_state(row.lifecycle, evaluated, row.created_at, cutoff)
        if lifecycle == row.lifecycle and evaluated == row.last_evaluated_at:
            continue
        # The state read above must still hold: a concurrent permanent mark or archive wins.
        result = await session.execute(
            update(Flag)
            .where(
                Flag.id == row.id,
                Flag.lifecycle == row.lifecycle,
                Flag.archived_at.is_(None),
            )
            .values(lifecycle=lifecycle, last_evaluated_at=evaluated, updated_at=Flag.updated_at)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0 or lifecycle == row.lifecycle:  # type: ignore[attr-defined]
            continue
        await record_system(
            session,
            "flag.lifecycle.update",
            "flag",
            row.key,
            project_id=row.project_id,
            before={"lifecycle": row.lifecycle.value},
            after={"lifecycle": lifecycle.value, "last_evaluated_at": evaluated},
        )
        changes += 1
        changed.add(row.project_id)
    return changes, changed


async def run_lifecycle_sweep(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime
) -> JobResult:
    async with sessionmaker() as session:
        changes, projects = await sweep(session, now)
        await session.commit()
    return JobResult(ok=changes, projects=projects)
