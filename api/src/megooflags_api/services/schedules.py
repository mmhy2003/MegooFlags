"""Scheduled changes: enable, disable or set the fallthrough rollout of a flag at a set time."""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.errors import ApiError, invalid_state, not_found, validation_error
from megooflags_api.models import Environment, Flag, FlagEnvironment, ScheduledChange
from megooflags_api.models.enums import ScheduledAction, ScheduleStatus
from megooflags_api.schemas.schedules import ScheduleIn, ScheduleOut
from megooflags_api.schemas.targeting import WeightIn
from megooflags_api.services import targeting
from megooflags_api.services.audit import Actor, AuditSink, SystemAuditor, record_flag_change
from megooflags_api.services.scheduler import MAX_PER_TICK, JobResult

logger = logging.getLogger(__name__)

MAX_AHEAD = timedelta(days=365)
LIST_LIMIT = 100


def schedule_out(row: ScheduledChange, env_key: str) -> ScheduleOut:
    return ScheduleOut(
        id=row.id,
        environment=env_key,
        action=row.action,
        weights=None if row.weights is None else [WeightIn.model_validate(w) for w in row.weights],
        run_at=row.run_at,
        status=row.status,
        error=row.error,
        created_by=row.created_by_label,
        change_request_id=row.change_request_id,
        created_at=row.created_at,
        executed_at=row.executed_at,
    )


async def create(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    body: ScheduleIn,
    actor: Actor,
    now: datetime,
    *,
    change_request_id: uuid.UUID | None = None,
) -> ScheduledChange:
    if body.run_at <= now:
        raise validation_error("run_at must be in the future", "run_at")
    if body.run_at > now + MAX_AHEAD:
        raise validation_error("run_at must be at most 365 days ahead", "run_at")
    if body.weights is not None:
        await targeting.check_fallthrough_weights(session, flag, body.weights)
    row = ScheduledChange(
        flag_environment_id=fe.id,
        action=body.action,
        weights=None if body.weights is None else [w.model_dump() for w in body.weights],
        run_at=body.run_at,
        created_by_id=actor.id,
        created_by_label=actor.label,
        change_request_id=change_request_id,
    )
    session.add(row)
    await session.flush()
    await record_flag_change(
        audit, "flag.schedule.create", flag, env, None, schedule_out(row, env.key)
    )
    return row


async def list_for(session: AsyncSession, fe: FlagEnvironment, env_key: str) -> list[ScheduleOut]:
    """The schedules of `fe`, latest `run_at` first, at most LIST_LIMIT."""
    rows = await session.scalars(
        select(ScheduledChange)
        .where(ScheduledChange.flag_environment_id == fe.id)
        .order_by(ScheduledChange.run_at.desc(), ScheduledChange.id.desc())
        .limit(LIST_LIMIT)
    )
    return [schedule_out(row, env_key) for row in rows]


async def get(
    session: AsyncSession, fe: FlagEnvironment, schedule_id: uuid.UUID, *, lock: bool = False
) -> ScheduledChange:
    """A schedule of `fe`; `lock` takes a row lock and reloads it."""
    query = select(ScheduledChange).where(
        ScheduledChange.id == schedule_id, ScheduledChange.flag_environment_id == fe.id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found("schedule", str(schedule_id))
    return row


async def cancel(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    schedule_id: uuid.UUID,
) -> ScheduleOut:
    row = await get(session, fe, schedule_id, lock=True)
    if row.status != ScheduleStatus.PENDING:
        raise invalid_state(f"schedule '{row.id}' is {row.status.value}")
    before = schedule_out(row, env.key)
    row.status = ScheduleStatus.CANCELLED
    await session.flush()
    out = schedule_out(row, env.key)
    await record_flag_change(audit, "flag.schedule.cancel", flag, env, before, out)
    return out


async def cancel_pending_for_flag(
    session: AsyncSession, audit: AuditSink, flag: Flag, reason: str
) -> int:
    """Cancel the pending schedules of `flag` in every environment; returns how many."""
    rows = await session.execute(
        select(ScheduledChange, Environment)
        .join(FlagEnvironment, FlagEnvironment.id == ScheduledChange.flag_environment_id)
        .join(Environment, Environment.id == FlagEnvironment.environment_id)
        .where(FlagEnvironment.flag_id == flag.id, ScheduledChange.status == ScheduleStatus.PENDING)
        .order_by(ScheduledChange.id)
        .with_for_update(of=ScheduledChange)
        .execution_options(populate_existing=True)
    )
    count = 0
    for row, env in rows.all():
        before = schedule_out(row, env.key)
        row.status = ScheduleStatus.CANCELLED
        await session.flush()
        await record_flag_change(
            audit,
            "flag.schedule.cancel",
            flag,
            env,
            before,
            schedule_out(row, env.key),
            reason=reason,
        )
        count += 1
    return count


async def _apply(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    row: ScheduledChange,
) -> None:
    if flag.archived_at is not None:
        raise invalid_state(f"flag '{flag.key}' is archived")
    context = {
        "schedule_id": row.id,
        "created_by": row.created_by_label,
        "change_request_id": row.change_request_id,
    }
    if row.action == ScheduledAction.SET_ROLLOUT:
        assert row.weights is not None
        before = await targeting.load_config(session, flag, fe, env.key)
        await targeting.set_fallthrough_rollout(
            session, flag, fe, [WeightIn.model_validate(w) for w in row.weights]
        )
        after = await targeting.load_config(session, flag, fe, env.key)
        await record_flag_change(audit, "flag.config.update", flag, env, before, after, **context)
    else:
        state = targeting.state_of(fe, env.key)
        await targeting.set_enabled(session, fe, row.action == ScheduledAction.ENABLE)
        await record_flag_change(
            audit, "flag.toggle", flag, env, state, targeting.state_of(fe, env.key), **context
        )


async def _fail(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    row: ScheduledChange,
    error: str,
    now: datetime,
) -> None:
    await session.refresh(row)  # the rolled-back savepoint expired it
    before = schedule_out(row, env.key)
    row.status, row.error, row.executed_at = ScheduleStatus.FAILED, error, now
    await session.flush()
    await record_flag_change(
        audit, "flag.schedule.fail", flag, env, before, schedule_out(row, env.key)
    )


async def _run_one(
    sessionmaker: async_sessionmaker[AsyncSession], schedule_id: uuid.UUID, now: datetime
) -> tuple[bool, uuid.UUID] | None:
    """Claim and apply one due schedule. Returns whether it was done and its project, or None
    when another run claimed it first."""
    async with sessionmaker() as session:
        fe_id = await session.scalar(
            select(ScheduledChange.flag_environment_id).where(ScheduledChange.id == schedule_id)
        )
        if fe_id is None:
            return None
        # The flag environment first, then the schedule: the order every writer uses.
        fe = await session.scalar(
            select(FlagEnvironment).where(FlagEnvironment.id == fe_id).with_for_update()
        )
        row = await session.scalar(
            select(ScheduledChange)
            .where(
                ScheduledChange.id == schedule_id,
                ScheduledChange.status == ScheduleStatus.PENDING,
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if fe is None or row is None:
            return None
        flag = await session.get_one(Flag, fe.flag_id)
        env = await session.get_one(Environment, fe.environment_id)
        audit = SystemAuditor(session)
        before = schedule_out(row, env.key)
        try:
            async with session.begin_nested():
                await _apply(session, audit, flag, env, fe, row)
                row.status, row.executed_at = ScheduleStatus.DONE, now
                await session.flush()
                await record_flag_change(
                    audit, "flag.schedule.run", flag, env, before, schedule_out(row, env.key)
                )
        except ApiError as exc:
            error = exc.message
        except Exception:
            logger.exception("scheduled change %s failed", schedule_id)
            error = "internal error"
        else:
            await session.commit()
            return True, env.project_id
        await _fail(session, audit, flag, env, row, error, now)
        await session.commit()
        return False, env.project_id


async def run_due_schedules(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime
) -> JobResult:
    """Apply the schedules due at `now`, each in its own transaction; overdue ones run late."""
    async with sessionmaker() as session:
        ids = list(
            await session.scalars(
                select(ScheduledChange.id)
                .where(
                    ScheduledChange.status == ScheduleStatus.PENDING, ScheduledChange.run_at <= now
                )
                .order_by(ScheduledChange.run_at, ScheduledChange.id)
                .limit(MAX_PER_TICK)
            )
        )
    result = JobResult()
    for schedule_id in ids:
        outcome = await _run_one(sessionmaker, schedule_id, now)
        if outcome is None:
            continue
        done, project_id = outcome
        if done:
            result.ok += 1
            result.projects.add(project_id)
        else:
            result.failed += 1
    return result
