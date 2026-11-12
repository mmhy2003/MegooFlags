"""Progressive rollouts: move the fallthrough from one variation to another in timed steps."""

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.errors import ApiError, invalid_reference, invalid_state
from megooflags_api.models import Environment, Flag, FlagEnvironment, ProgressiveRollout, Variation
from megooflags_api.models.enums import RolloutStatus
from megooflags_api.schemas.rollouts import (
    FULL,
    ProgressiveRolloutIn,
    ProgressiveRolloutOut,
    RolloutStepIn,
    RolloutView,
)
from megooflags_api.schemas.targeting import WeightIn
from megooflags_api.services import targeting
from megooflags_api.services.audit import Actor, AuditSink, SystemAuditor, record_flag_change
from megooflags_api.services.scheduler import MAX_PER_TICK, JobResult

logger = logging.getLogger(__name__)

LIVE = (RolloutStatus.ACTIVE, RolloutStatus.PAUSED)
RECENT_LIMIT = 10
VARIATION_DELETED = "variation was deleted"


async def _variation_keys(
    session: AsyncSession, row: ProgressiveRollout
) -> tuple[str | None, str | None]:
    ids = [i for i in (row.from_variation_id, row.to_variation_id) if i is not None]
    keys: dict[uuid.UUID, str] = (
        {
            id_: key
            for id_, key in await session.execute(
                select(Variation.id, Variation.key).where(Variation.id.in_(ids))
            )
        }
        if ids
        else {}
    )
    return (
        None if row.from_variation_id is None else keys.get(row.from_variation_id),
        None if row.to_variation_id is None else keys.get(row.to_variation_id),
    )


async def to_out(
    session: AsyncSession, row: ProgressiveRollout, env_key: str
) -> ProgressiveRolloutOut:
    from_key, to_key = await _variation_keys(session, row)
    return ProgressiveRolloutOut(
        id=row.id,
        environment=env_key,
        from_variation=from_key,
        to_variation=to_key,
        steps=[RolloutStepIn.model_validate(step) for step in row.steps],
        current_step=row.current_step,
        next_step_at=row.next_step_at,
        remaining_seconds=row.remaining_seconds,
        status=row.status,
        last_applied_weights=None
        if row.last_applied_weights is None
        else [WeightIn.model_validate(w) for w in row.last_applied_weights],
        error=row.error,
        created_by=row.created_by_label,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finished_at=row.finished_at,
    )


def _context(row: ProgressiveRollout, **extra: Any) -> dict[str, Any]:
    return {"rollout_id": row.id, "created_by": row.created_by_label, **extra}


async def _apply_step(
    session: AsyncSession,
    flag: Flag,
    fe: FlagEnvironment,
    row: ProgressiveRollout,
    index: int,
    now: datetime,
) -> None:
    """Serve step `index` as the fallthrough of `fe`; the last step serves the "to" variation
    and completes the rollout."""
    assert row.from_variation_id is not None and row.to_variation_id is not None
    step = row.steps[index]
    row.current_step = index
    if step["percentage"] >= FULL:
        await targeting.set_fallthrough_variation(session, fe, row.to_variation_id)
        row.status, row.finished_at = RolloutStatus.COMPLETED, now
        row.next_step_at, row.last_applied_weights = None, None
    else:
        from_key, to_key = await _variation_keys(session, row)
        assert from_key is not None and to_key is not None
        weights = [
            WeightIn(variation=from_key, weight=FULL - step["percentage"]),
            WeightIn(variation=to_key, weight=step["percentage"]),
        ]
        await targeting.set_fallthrough_rollout(session, flag, fe, weights)
        row.last_applied_weights = [w.model_dump() for w in weights]
        row.next_step_at = now + timedelta(minutes=step["wait_minutes"])
    await session.flush()


async def start(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    body: ProgressiveRolloutIn,
    actor: Actor,
    now: datetime,
    *,
    change_request_id: uuid.UUID | None = None,
) -> ProgressiveRollout:
    """Start a rollout on `fe`, which the caller has locked, and apply its first step at once."""
    if await _live(session, fe) is not None:
        raise invalid_state(f"a rollout is already running for flag '{flag.key}' in '{env.key}'")
    variation_ids: dict[str, uuid.UUID] = {
        key: id_
        for key, id_ in await session.execute(
            select(Variation.key, Variation.id).where(Variation.flag_id == flag.id)
        )
    }
    for path, key in (("from_variation", body.from_variation), ("to_variation", body.to_variation)):
        if key not in variation_ids:
            raise invalid_reference(f"unknown variation '{key}'", path)
    row = ProgressiveRollout(
        flag_environment_id=fe.id,
        from_variation_id=variation_ids[body.from_variation],
        to_variation_id=variation_ids[body.to_variation],
        steps=[step.model_dump() for step in body.steps],
        created_by_id=actor.id,
        created_by_label=actor.label,
    )
    session.add(row)
    await session.flush()
    before = await targeting.load_config(session, flag, fe, env.key)
    await _apply_step(session, flag, fe, row, 0, now)
    context = _context(row, change_request_id=change_request_id)
    out = await to_out(session, row, env.key)
    await record_flag_change(audit, "flag.rollout.start", flag, env, None, out, **context)
    after = await targeting.load_config(session, flag, fe, env.key)
    await record_flag_change(audit, "flag.config.update", flag, env, before, after, **context)
    if row.status == RolloutStatus.COMPLETED:
        await record_flag_change(audit, "flag.rollout.complete", flag, env, None, out, **context)
    return row


async def _live(
    session: AsyncSession, fe: FlagEnvironment, *, lock: bool = False
) -> ProgressiveRollout | None:
    query = select(ProgressiveRollout).where(
        ProgressiveRollout.flag_environment_id == fe.id, ProgressiveRollout.status.in_(LIVE)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def live(session: AsyncSession, fe: FlagEnvironment) -> ProgressiveRollout:
    """The active or paused rollout of `fe`, locked; invalid_state when there is none."""
    row = await _live(session, fe, lock=True)
    if row is None:
        raise invalid_state("no rollout is running in this environment")
    return row


async def view(session: AsyncSession, fe: FlagEnvironment, env_key: str) -> RolloutView:
    current = await _live(session, fe)
    recent = await session.scalars(
        select(ProgressiveRollout)
        .where(
            ProgressiveRollout.flag_environment_id == fe.id,
            ProgressiveRollout.status.not_in(LIVE),
        )
        .order_by(ProgressiveRollout.created_at.desc(), ProgressiveRollout.id.desc())
        .limit(RECENT_LIMIT)
    )
    return RolloutView(
        current=None if current is None else await to_out(session, current, env_key),
        recent=[await to_out(session, row, env_key) for row in recent.all()],
    )


async def _pause(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    row: ProgressiveRollout,
    now: datetime,
) -> None:
    assert row.next_step_at is not None
    before = await to_out(session, row, env.key)
    row.status = RolloutStatus.PAUSED
    row.remaining_seconds = max(0, math.ceil((row.next_step_at - now).total_seconds()))
    row.next_step_at = None
    await session.flush()
    after = await to_out(session, row, env.key)
    await record_flag_change(audit, "flag.rollout.pause", flag, env, before, after, **_context(row))


async def pause(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    now: datetime,
) -> ProgressiveRollout:
    """Stop the clock of the active rollout; the wait left is kept for resume."""
    row = await live(session, fe)
    if row.status != RolloutStatus.ACTIVE:
        raise invalid_state(f"rollout '{row.id}' is {row.status.value}")
    await _pause(session, audit, flag, env, row, now)
    return row


async def pause_live(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    now: datetime,
) -> ProgressiveRollout | None:
    """Pause the rollout of `fe` if one is active; returns it, or None."""
    row = await _live(session, fe, lock=True)
    if row is None or row.status != RolloutStatus.ACTIVE:
        return None
    await _pause(session, audit, flag, env, row, now)
    return row


async def resume(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    now: datetime,
) -> ProgressiveRollout:
    """Restart the clock of a paused rollout with the wait it had left."""
    row = await live(session, fe)
    if row.status != RolloutStatus.PAUSED:
        raise invalid_state(f"rollout '{row.id}' is {row.status.value}")
    before = await to_out(session, row, env.key)
    row.status = RolloutStatus.ACTIVE
    row.next_step_at = now + timedelta(seconds=row.remaining_seconds or 0)
    row.remaining_seconds = None
    await session.flush()
    after = await to_out(session, row, env.key)
    await record_flag_change(
        audit, "flag.rollout.resume", flag, env, before, after, **_context(row)
    )
    return row


async def abort(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    now: datetime,
    *,
    reason: str | None = None,
) -> ProgressiveRollout:
    """Stop the rollout and serve the "from" variation again, or leave the fallthrough as it is
    when that variation was deleted."""
    row = await live(session, fe)
    before = await to_out(session, row, env.key)
    context = _context(row, reason=reason)
    if row.from_variation_id is None:
        row.error = f"{VARIATION_DELETED}; the fallthrough was left as it is"
    else:
        config_before = await targeting.load_config(session, flag, fe, env.key)
        await targeting.set_fallthrough_variation(session, fe, row.from_variation_id)
        config_after = await targeting.load_config(session, flag, fe, env.key)
        await record_flag_change(
            audit, "flag.config.update", flag, env, config_before, config_after, **context
        )
    row.status, row.finished_at = RolloutStatus.ABORTED, now
    row.next_step_at, row.remaining_seconds = None, None
    await session.flush()
    after = await to_out(session, row, env.key)
    await record_flag_change(audit, "flag.rollout.abort", flag, env, before, after, **context)
    return row


async def cancel_live_for_flag(
    session: AsyncSession, audit: AuditSink, flag: Flag, reason: str
) -> int:
    """Cancel the live rollouts of `flag` in every environment, leaving the fallthrough as it is;
    returns how many."""
    rows = await session.execute(
        select(ProgressiveRollout, Environment)
        .join(FlagEnvironment, FlagEnvironment.id == ProgressiveRollout.flag_environment_id)
        .join(Environment, Environment.id == FlagEnvironment.environment_id)
        .where(FlagEnvironment.flag_id == flag.id, ProgressiveRollout.status.in_(LIVE))
        .order_by(ProgressiveRollout.id)
        .with_for_update(of=ProgressiveRollout)
        .execution_options(populate_existing=True)
    )
    count = 0
    for row, env in rows.all():
        before = await to_out(session, row, env.key)
        row.status, row.finished_at = RolloutStatus.CANCELLED, datetime.now(UTC)
        row.next_step_at, row.remaining_seconds = None, None
        await session.flush()
        after = await to_out(session, row, env.key)
        await record_flag_change(
            audit, "flag.rollout.cancel", flag, env, before, after, **_context(row, reason=reason)
        )
        count += 1
    return count


async def _advance(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    row: ProgressiveRollout,
    now: datetime,
) -> None:
    if flag.archived_at is not None:
        raise invalid_state(f"flag '{flag.key}' is archived")
    if row.from_variation_id is None or row.to_variation_id is None:
        raise invalid_state(VARIATION_DELETED)
    expected = {w["variation"]: w["weight"] for w in row.last_applied_weights or []}
    if await targeting.fallthrough_weights(session, fe) != expected:
        raise invalid_state("fallthrough changed manually")
    before = await targeting.load_config(session, flag, fe, env.key)
    await _apply_step(session, flag, fe, row, row.current_step + 1, now)
    after = await targeting.load_config(session, flag, fe, env.key)
    context = _context(row)
    await record_flag_change(audit, "flag.config.update", flag, env, before, after, **context)
    action = (
        "flag.rollout.complete" if row.status == RolloutStatus.COMPLETED else "flag.rollout.step"
    )
    await record_flag_change(
        audit, action, flag, env, None, await to_out(session, row, env.key), **context
    )


async def _fail(
    session: AsyncSession,
    audit: AuditSink,
    flag: Flag,
    env: Environment,
    row: ProgressiveRollout,
    error: str,
    now: datetime,
) -> None:
    await session.refresh(row)  # the rolled-back savepoint expired it
    before = await to_out(session, row, env.key)
    row.status, row.error, row.finished_at = RolloutStatus.FAILED, error, now
    row.next_step_at = None
    await session.flush()
    after = await to_out(session, row, env.key)
    await record_flag_change(audit, "flag.rollout.fail", flag, env, before, after, **_context(row))


async def _run_one(
    sessionmaker: async_sessionmaker[AsyncSession], rollout_id: uuid.UUID, now: datetime
) -> tuple[bool, uuid.UUID] | None:
    """Claim and advance one due rollout. Returns whether it stepped and its project, or None
    when another run claimed it first."""
    async with sessionmaker() as session:
        fe_id = await session.scalar(
            select(ProgressiveRollout.flag_environment_id).where(
                ProgressiveRollout.id == rollout_id
            )
        )
        if fe_id is None:
            return None
        # The flag environment first, then the rollout: the order every writer uses.
        fe = await session.scalar(
            select(FlagEnvironment).where(FlagEnvironment.id == fe_id).with_for_update()
        )
        # Still due: a run that got the lock first has moved next_step_at on.
        row = await session.scalar(
            select(ProgressiveRollout)
            .where(
                ProgressiveRollout.id == rollout_id,
                ProgressiveRollout.status == RolloutStatus.ACTIVE,
                ProgressiveRollout.next_step_at <= now,
            )
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if fe is None or row is None:
            return None
        flag = await session.get_one(Flag, fe.flag_id)
        env = await session.get_one(Environment, fe.environment_id)
        audit = SystemAuditor(session)
        try:
            async with session.begin_nested():
                await _advance(session, audit, flag, env, fe, row, now)
        except ApiError as exc:
            error = exc.message
        except Exception:
            logger.exception("progressive rollout %s failed", rollout_id)
            error = "internal error"
        else:
            await session.commit()
            return True, env.project_id
        await _fail(session, audit, flag, env, row, error, now)
        await session.commit()
        return False, env.project_id


async def run_due_rollouts(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime
) -> JobResult:
    """Apply the rollout steps due at `now`, each in its own transaction; overdue ones run late,
    and the next step is then due a full wait later."""
    async with sessionmaker() as session:
        ids = list(
            await session.scalars(
                select(ProgressiveRollout.id)
                .where(
                    ProgressiveRollout.status == RolloutStatus.ACTIVE,
                    ProgressiveRollout.next_step_at <= now,
                )
                .order_by(ProgressiveRollout.next_step_at, ProgressiveRollout.id)
                .limit(MAX_PER_TICK)
            )
        )
    result = JobResult()
    for rollout_id in ids:
        outcome = await _run_one(sessionmaker, rollout_id, now)
        if outcome is None:
            continue
        stepped, project_id = outcome
        if stepped:
            result.ok += 1
            result.projects.add(project_id)
        else:
            result.failed += 1
    return result
