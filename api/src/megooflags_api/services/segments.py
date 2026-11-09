"""Segment CRUD and the whole-config replace of its included/excluded keys and rules."""

import uuid

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import in_use, key_conflict, validation_error
from megooflags_api.models import Project, Segment, SegmentRule, SegmentTarget
from megooflags_api.models.enums import SegmentTargetKind
from megooflags_api.schemas.segments import (
    SegmentConfigIn,
    SegmentCreate,
    SegmentOut,
    SegmentRuleOut,
    SegmentSummary,
    SegmentUpdate,
)
from megooflags_api.services.clauses import check_clause, insert_clauses, load_clauses
from megooflags_api.services.flags import like_pattern
from megooflags_api.services.pagination import decode_cursor, page_slice
from megooflags_api.services.usage import segment_dependents


async def create_segment(session: AsyncSession, project: Project, body: SegmentCreate) -> Segment:
    segment = Segment(
        project_id=project.id, key=body.key, name=body.name, description=body.description
    )
    session.add(segment)
    try:
        await session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise key_conflict("segment", body.key) from None
        raise
    return segment


async def list_segments(
    session: AsyncSession, project: Project, *, q: str | None, cursor: str | None, limit: int
) -> tuple[list[SegmentSummary], str | None]:
    query = (
        select(Segment)
        .where(Segment.project_id == project.id)
        .order_by(Segment.key)
        .limit(limit + 1)
    )
    if q:
        pattern = like_pattern(q)
        query = query.where(
            or_(Segment.key.ilike(pattern, escape="\\"), Segment.name.ilike(pattern, escape="\\"))
        )
    if cursor is not None:
        query = query.where(Segment.key > decode_cursor(cursor))
    rows, next_cursor = page_slice(list(await session.scalars(query)), limit, lambda s: s.key)
    return [SegmentSummary.model_validate(s, from_attributes=True) for s in rows], next_cursor


async def update_segment(session: AsyncSession, segment: Segment, body: SegmentUpdate) -> None:
    if body.name is not None:
        segment.name = body.name
    if body.description is not None:
        segment.description = body.description
    await session.flush()


async def delete_segment(session: AsyncSession, segment: Segment) -> None:
    """Refused while a flag rule uses the segment; its targets and rules go with it."""
    dependents = await segment_dependents(session, segment.id)
    if dependents:
        raise in_use(f"segment '{segment.key}' is used by flag rules", dependents)
    await session.delete(segment)
    await session.flush()


def _check_config(body: SegmentConfigIn) -> tuple[list[str], list[str]]:
    """The deduplicated, sorted lists; a key in both lists is a validation error."""
    included = set(body.included)
    for j, key in enumerate(body.excluded):
        if key in included:
            raise validation_error(
                f"context key '{key}' is both included and excluded", f"excluded[{j}]"
            )
    for i, rule in enumerate(body.rules):
        for k, clause in enumerate(rule.clauses):
            check_clause(clause, f"rules[{i}].clauses[{k}]", allow_segments=False)
    return sorted(included), sorted(set(body.excluded))


async def replace_segment_config(
    session: AsyncSession, segment: Segment, body: SegmentConfigIn
) -> None:
    included, excluded = _check_config(body)
    await session.execute(delete(SegmentTarget).where(SegmentTarget.segment_id == segment.id))
    await session.execute(delete(SegmentRule).where(SegmentRule.segment_id == segment.id))
    targets = [
        {"segment_id": segment.id, "context_key": key, "kind": kind}
        for kind, keys in (
            (SegmentTargetKind.INCLUDED, included),
            (SegmentTargetKind.EXCLUDED, excluded),
        )
        for key in keys
    ]
    if targets:
        await session.execute(insert(SegmentTarget), targets)
    rule_ids = [uuid.uuid4() for _ in body.rules]
    session.add_all(
        SegmentRule(id=rule_id, segment_id=segment.id, position=i, description=rule.description)
        for i, (rule_id, rule) in enumerate(zip(rule_ids, body.rules, strict=True))
    )
    await session.flush()
    for rule_id, rule in zip(rule_ids, body.rules, strict=True):
        await insert_clauses(
            session, rule.clauses, flag_rule_id=None, segment_rule_id=rule_id, segment_ids={}
        )
    segment.updated_at = func.now()
    await session.flush()
    await session.refresh(segment)  # load the updated_at the database set


async def segment_out(session: AsyncSession, segment: Segment) -> SegmentOut:
    """One query for the targets, one for the rules, then the clauses of all rules."""
    targets = await session.execute(
        select(SegmentTarget.kind, SegmentTarget.context_key)
        .where(SegmentTarget.segment_id == segment.id)
        .order_by(SegmentTarget.context_key)
    )
    lists: dict[SegmentTargetKind, list[str]] = {kind: [] for kind in SegmentTargetKind}
    for kind, key in targets:
        lists[kind].append(key)
    rules = list(
        await session.execute(
            select(SegmentRule.id, SegmentRule.description)
            .where(SegmentRule.segment_id == segment.id)
            .order_by(SegmentRule.position)
        )
    )
    clauses = await load_clauses(session, segment_rule_ids=[rule.id for rule in rules])
    summary = SegmentSummary.model_validate(segment, from_attributes=True)
    return SegmentOut(
        **summary.model_dump(),
        included=lists[SegmentTargetKind.INCLUDED],
        excluded=lists[SegmentTargetKind.EXCLUDED],
        rules=[
            SegmentRuleOut(description=rule.description, clauses=clauses.get(rule.id, []))
            for rule in rules
        ],
    )
