"""Change requests: a flag change in one environment that waits for approvals."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.rbac import Permission
from megooflags_api.errors import (
    ApiError,
    invalid_state,
    not_found,
    validation_error,
    version_conflict,
)
from megooflags_api.models import (
    ChangeRequest,
    ChangeRequestComment,
    ChangeRequestReview,
    Environment,
    Flag,
    FlagEnvironment,
    Project,
)
from megooflags_api.models.enums import (
    ChangeRequestKind,
    ChangeRequestStatus,
    ReviewDecision,
    ScheduledAction,
)
from megooflags_api.schemas.change_requests import (
    ChangeRequestCreate,
    ChangeRequestOut,
    ChangeRequestSummary,
    CommentOut,
    ReviewOut,
)
from megooflags_api.schemas.rollouts import ProgressiveRolloutIn
from megooflags_api.schemas.schedules import ScheduleIn
from megooflags_api.schemas.targeting import FlagConfigIn, ToggleIn
from megooflags_api.services import approvals, rollouts, schedules, targeting
from megooflags_api.services.audit import (
    Actor,
    AuditSink,
    SystemAuditor,
    record_flag_change,
    snapshot,
)
from megooflags_api.services.lookup import get_flag_environment
from megooflags_api.services.pagination import decode_cursor, page_slice

PAYLOADS: dict[ChangeRequestKind, type[BaseModel]] = {
    ChangeRequestKind.CONFIG: FlagConfigIn,
    ChangeRequestKind.TOGGLE: ToggleIn,
    ChangeRequestKind.SCHEDULE: ScheduleIn,
    ChangeRequestKind.ROLLOUT: ProgressiveRolloutIn,
}


def _path(loc: tuple[int | str, ...]) -> str:
    path = "payload"
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def parse_payload(kind: ChangeRequestKind, payload: dict[str, Any]) -> BaseModel:
    """`payload` as the input schema of the direct endpoint for `kind`; 422 at `payload.<loc>`."""
    try:
        return PAYLOADS[kind].model_validate(payload)
    except ValidationError as exc:
        error = exc.errors()[0]
        raise validation_error(error["msg"], _path(error["loc"])) from None


def required_permission(kind: ChangeRequestKind, payload: BaseModel) -> Permission:
    """The permission of the direct endpoint the change request stands in for."""
    if kind == ChangeRequestKind.TOGGLE:
        return Permission.FLAG_TOGGLE
    if isinstance(payload, ScheduleIn) and payload.action != ScheduledAction.SET_ROLLOUT:
        return Permission.FLAG_TOGGLE
    return Permission.FLAG_CONFIG_WRITE


async def apply_payload(
    session: AsyncSession,
    audit: AuditSink,
    cr: ChangeRequest,
    flag: Flag,
    env: Environment,
    fe: FlagEnvironment,
    now: datetime,
) -> None:
    """Make the change of `cr` on `fe`, which the caller has locked, as its author would.

    The base version is not checked here; the audit entries carry the change request's id.
    """
    payload = parse_payload(cr.kind, cr.payload)
    author = Actor(cr.author_id, cr.author_label)
    context = {"change_request_id": cr.id, "created_by": cr.author_label}
    if isinstance(payload, FlagConfigIn):
        before = await targeting.load_config(session, flag, fe, env.key)
        await targeting.replace_config(session, flag, fe, payload, check_version=False)
        after = await targeting.load_config(session, flag, fe, env.key)
        await record_flag_change(audit, "flag.config.update", flag, env, before, after, **context)
    elif isinstance(payload, ToggleIn):
        state = targeting.state_of(fe, env.key)
        await targeting.set_enabled(session, fe, payload.enabled)
        await record_flag_change(
            audit, "flag.toggle", flag, env, state, targeting.state_of(fe, env.key), **context
        )
    elif isinstance(payload, ScheduleIn):
        await schedules.create(
            session, audit, flag, env, fe, payload, author, now, change_request_id=cr.id
        )
    else:
        assert isinstance(payload, ProgressiveRolloutIn)
        await rollouts.start(
            session, audit, flag, env, fe, payload, author, now, change_request_id=cr.id
        )


def _in_payload(exc: ApiError) -> ApiError:
    """`exc` with its `path` detail, if any, pointing into the request's payload."""
    if isinstance(exc.details, dict) and isinstance(exc.details.get("path"), str):
        exc.details = {**exc.details, "path": f"payload.{exc.details['path']}"}
    return exc


async def create(
    session: AsyncSession,
    audit: AuditSink,
    actor: Actor,
    project: Project,
    env: Environment,
    flag: Flag,
    body: ChangeRequestCreate,
    now: datetime,
) -> ChangeRequest:
    """Open a change request after a dry run of its change, so an invalid one is refused now."""
    payload = parse_payload(body.kind, body.payload)
    fe = await get_flag_environment(session, flag, env, lock=True)
    if isinstance(payload, FlagConfigIn) and payload.version != fe.version:
        raise version_conflict(fe.version)
    cr = ChangeRequest(
        project_id=project.id,
        environment_id=env.id,
        flag_id=flag.id,
        kind=body.kind,
        payload=payload.model_dump(mode="json", exclude_unset=True),
        base_version=fe.version,
        title=body.title,
        description=body.description,
        author_id=actor.id,
        author_label=actor.label,
    )
    session.add(cr)
    await session.flush()

    savepoint = await session.begin_nested()
    try:
        await apply_payload(session, SystemAuditor(session), cr, flag, env, fe, now)
    except ApiError as exc:
        raise _in_payload(exc) from None
    finally:
        await savepoint.rollback()
        await session.refresh(fe)

    await audit.record(
        "change_request.create",
        "change_request",
        str(cr.id),
        project_id=project.id,
        environment_id=env.id,
        after=await _snapshot(session, cr),
    )
    return cr


def _approvals() -> Any:
    """The number of approving reviews of the outer query's change request."""
    return (
        select(func.count())
        .where(
            ChangeRequestReview.change_request_id == ChangeRequest.id,
            ChangeRequestReview.decision == ReviewDecision.APPROVE,
        )
        .correlate(ChangeRequest)
        .scalar_subquery()
    )


def _summary(
    cr: ChangeRequest, env: Environment, flag_key: str, approvals_count: int
) -> ChangeRequestSummary:
    return ChangeRequestSummary(
        id=cr.id,
        environment=env.key,
        flag=flag_key,
        kind=cr.kind,
        title=cr.title,
        status=cr.status,
        author=cr.author_label,
        approvals=approvals_count,
        approvals_required=approvals.required_approvals(env),
        created_at=cr.created_at,
        closed_at=cr.closed_at,
    )


async def summary(session: AsyncSession, cr: ChangeRequest) -> ChangeRequestSummary:
    env = await session.get_one(Environment, cr.environment_id)
    flag = await session.get_one(Flag, cr.flag_id)
    count = await session.scalar(select(_approvals()).where(ChangeRequest.id == cr.id))
    return _summary(cr, env, flag.key, count or 0)


async def _snapshot(session: AsyncSession, cr: ChangeRequest) -> dict[str, Any]:
    """What audit entries keep of `cr`."""
    return snapshot(await summary(session, cr)) | {
        "description": cr.description,
        "payload": cr.payload,
        "base_version": cr.base_version,
    }


def _cursor_key(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at, _, id_ = decode_cursor(cursor).partition("|")
    try:
        return datetime.fromisoformat(created_at), uuid.UUID(id_)
    except ValueError:
        raise validation_error("invalid cursor", "cursor") from None


async def list_change_requests(
    session: AsyncSession,
    project: Project,
    *,
    visible: set[uuid.UUID] | None,
    status: ChangeRequestStatus | None,
    env: str | None,
    flag: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[ChangeRequestSummary], str | None]:
    """One page, newest first, keyed on (created_at, id); `visible` limits the environment ids
    (None means all). `env` and `flag` are keys."""
    query = (
        select(ChangeRequest, Environment, Flag.key, _approvals())
        .join(Environment, Environment.id == ChangeRequest.environment_id)
        .join(Flag, Flag.id == ChangeRequest.flag_id)
        .where(ChangeRequest.project_id == project.id)
        .order_by(ChangeRequest.created_at.desc(), ChangeRequest.id.desc())
        .limit(limit + 1)
    )
    if visible is not None:
        query = query.where(ChangeRequest.environment_id.in_(visible))
    if status is not None:
        query = query.where(ChangeRequest.status == status)
    if env is not None:
        query = query.where(Environment.key == env)
    if flag is not None:
        query = query.where(Flag.key == flag)
    if cursor is not None:
        query = query.where(
            tuple_(ChangeRequest.created_at, ChangeRequest.id) < tuple_(*_cursor_key(cursor))
        )
    rows, next_cursor = page_slice(
        list(await session.execute(query)),
        limit,
        lambda row: f"{row[0].created_at.isoformat()}|{row[0].id}",
    )
    items = [_summary(cr, env_row, flag_key, count) for cr, env_row, flag_key, count in rows]
    return items, next_cursor


async def get(
    session: AsyncSession,
    project: Project,
    cr_id: uuid.UUID,
    visible: set[uuid.UUID] | None,
    *,
    lock: bool = False,
) -> ChangeRequest:
    """A change request of `project` in a visible environment (else 404); `lock` takes a row
    lock and reloads it."""
    query = select(ChangeRequest).where(
        ChangeRequest.id == cr_id, ChangeRequest.project_id == project.id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    cr = await session.scalar(query)
    if cr is None or (visible is not None and cr.environment_id not in visible):
        raise not_found("change request", str(cr_id))
    return cr


def _comment_out(row: ChangeRequestComment) -> CommentOut:
    return CommentOut(id=row.id, author=row.author_label, body=row.body, created_at=row.created_at)


async def out(session: AsyncSession, cr: ChangeRequest) -> ChangeRequestOut:
    """`cr` with its reviews and comments, oldest first, and for config the current and
    proposed config."""
    reviews = await session.scalars(
        select(ChangeRequestReview)
        .where(ChangeRequestReview.change_request_id == cr.id)
        .order_by(ChangeRequestReview.created_at, ChangeRequestReview.id)
    )
    comments = await session.scalars(
        select(ChangeRequestComment)
        .where(ChangeRequestComment.change_request_id == cr.id)
        .order_by(ChangeRequestComment.created_at, ChangeRequestComment.id)
    )
    current, proposed = None, None
    if cr.kind == ChangeRequestKind.CONFIG:
        env = await session.get_one(Environment, cr.environment_id)
        flag = await session.get_one(Flag, cr.flag_id)
        fe = await get_flag_environment(session, flag, env)
        current = await targeting.load_config(session, flag, fe, env.key)
        proposed = FlagConfigIn.model_validate(cr.payload)
    return ChangeRequestOut(
        **(await summary(session, cr)).model_dump(),
        description=cr.description,
        payload=cr.payload,
        base_version=cr.base_version,
        error=cr.error,
        reviews=[
            ReviewOut(
                reviewer=r.reviewer_label,
                decision=r.decision,
                comment=r.comment,
                created_at=r.created_at,
            )
            for r in reviews.all()
        ],
        comments=[_comment_out(c) for c in comments.all()],
        current=current,
        proposed=proposed,
    )


def _ensure_open(cr: ChangeRequest) -> None:
    if cr.status != ChangeRequestStatus.OPEN:
        raise invalid_state(f"change request '{cr.id}' is {cr.status.value}")


async def comment(
    session: AsyncSession, audit: AuditSink, actor: Actor, cr: ChangeRequest, body: str
) -> CommentOut:
    """Comment on `cr`, which the caller has locked; 409 once it is closed."""
    _ensure_open(cr)
    row = ChangeRequestComment(
        change_request_id=cr.id, author_id=actor.id, author_label=actor.label, body=body
    )
    session.add(row)
    await session.flush()
    result = _comment_out(row)
    await audit.record(
        "change_request.comment",
        "change_request",
        str(cr.id),
        project_id=cr.project_id,
        environment_id=cr.environment_id,
        after=snapshot(result),
    )
    return result


async def cancel(
    session: AsyncSession, audit: AuditSink, cr: ChangeRequest, now: datetime
) -> ChangeRequest:
    """Close `cr`, which the caller has locked, without applying it; 409 once it is closed."""
    _ensure_open(cr)
    before = await _snapshot(session, cr)
    cr.status, cr.closed_at = ChangeRequestStatus.CANCELLED, now
    await session.flush()
    await audit.record(
        "change_request.cancel",
        "change_request",
        str(cr.id),
        project_id=cr.project_id,
        environment_id=cr.environment_id,
        before=before,
        after=await _snapshot(session, cr),
    )
    return cr
