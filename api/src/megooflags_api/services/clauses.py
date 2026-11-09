"""Clauses of flag rules and segment rules: operator value checks and storage."""

import math
import re
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import invalid_reference, validation_error
from megooflags_api.models import Clause, ClauseSegment, Segment
from megooflags_api.models.enums import Operator
from megooflags_api.schemas.targeting import ClauseIn, ClauseOut

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")

STRING_OPERATORS = {Operator.CONTAINS, Operator.STARTS_WITH, Operator.ENDS_WITH}
NUMBER_OPERATORS = {Operator.LT, Operator.LTE, Operator.GT, Operator.GTE}
SEMVER_OPERATORS = {Operator.SEMVER_EQ, Operator.SEMVER_LT, Operator.SEMVER_GT}
DATE_OPERATORS = {Operator.BEFORE, Operator.AFTER}


def _is_regex(value: str) -> bool:
    try:
        re.compile(value)
    except re.error:
        return False
    return True


def _is_datetime(value: str) -> bool:
    if not RFC3339.match(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _value_error(operator: Operator, value: Any) -> str | None:
    """Why `value` is not valid for `operator`, or None if it is."""
    if operator in (Operator.IN, Operator.NOT_IN):
        return None
    if operator in NUMBER_OPERATORS:
        if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
            return None
        return f"{operator.value} needs finite numbers"
    if not isinstance(value, str):
        return f"{operator.value} needs strings"
    if operator == Operator.MATCHES and not _is_regex(value):
        return "invalid regular expression"
    if operator in SEMVER_OPERATORS and not SEMVER.match(value):
        return "invalid semantic version"
    if operator in DATE_OPERATORS and not _is_datetime(value):
        return "invalid RFC 3339 date-time"
    return None


def check_clause(clause: ClauseIn, path: str, *, allow_segments: bool) -> None:
    """Raise 422 `validation_error` unless every value suits the clause operator."""
    if clause.operator == Operator.IN_SEGMENT and not allow_segments:
        raise validation_error("segment rules cannot use in_segment", f"{path}.operator")
    for i, value in enumerate(clause.values):
        message = _value_error(clause.operator, value)
        if message is not None:
            raise validation_error(message, f"{path}.values[{i}]")


async def resolve_segments(
    session: AsyncSession, project_id: uuid.UUID, clauses_by_path: list[tuple[str, ClauseIn]]
) -> dict[str, uuid.UUID]:
    """Map every segment key used by an in_segment clause to its id, in one query."""
    refs = [
        (f"{path}.values[{i}]", str(key))
        for path, clause in clauses_by_path
        if clause.operator == Operator.IN_SEGMENT
        for i, key in enumerate(clause.values)
    ]
    if not refs:
        return {}
    rows = await session.execute(
        select(Segment.key, Segment.id).where(
            Segment.project_id == project_id, Segment.key.in_({key for _, key in refs})
        )
    )
    ids: dict[str, uuid.UUID] = {key: id_ for key, id_ in rows.tuples()}
    for path, key in refs:
        if key not in ids:
            raise invalid_reference(f"unknown segment '{key}'", path)
    return ids


async def insert_clauses(
    session: AsyncSession,
    clauses: list[ClauseIn],
    *,
    flag_rule_id: uuid.UUID | None,
    segment_rule_id: uuid.UUID | None,
    segment_ids: dict[str, uuid.UUID],
) -> None:
    """Store `clauses` in order under one rule. in_segment keys go to clause_segment."""
    links: list[dict[str, uuid.UUID]] = []
    for position, clause in enumerate(clauses):
        in_segment = clause.operator == Operator.IN_SEGMENT
        row = Clause(
            id=uuid.uuid4(),
            flag_rule_id=flag_rule_id,
            segment_rule_id=segment_rule_id,
            position=position,
            attribute=clause.attribute,
            operator=clause.operator,
            values=[] if in_segment else list(clause.values),
            negate=clause.negate,
        )
        session.add(row)
        if in_segment:
            segment_keys = {str(key) for key in clause.values}
            links.extend(
                {"clause_id": row.id, "segment_id": segment_ids[key]}
                for key in sorted(segment_keys)
            )
    await session.flush()
    if links:
        await session.execute(insert(ClauseSegment), links)


async def load_clauses(
    session: AsyncSession,
    *,
    flag_rule_ids: list[uuid.UUID] | None = None,
    segment_rule_ids: list[uuid.UUID] | None = None,
) -> dict[uuid.UUID, list[ClauseOut]]:
    """The clauses of each given rule, in position order, keyed by rule id.

    The clause query runs even for an empty list, so callers issue the same statements
    whatever the number of rules."""
    if flag_rule_ids is not None:
        owner = Clause.flag_rule_id
        rule_ids = flag_rule_ids
    elif segment_rule_ids is not None:
        owner = Clause.segment_rule_id
        rule_ids = segment_rule_ids
    else:
        return {}
    clauses = (
        await session.scalars(
            select(Clause).where(owner.in_(rule_ids)).order_by(owner, Clause.position)
        )
    ).all()
    segment_keys: dict[uuid.UUID, list[str]] = defaultdict(list)
    segment_clause_ids = [c.id for c in clauses if c.operator == Operator.IN_SEGMENT]
    if segment_clause_ids:
        rows = await session.execute(
            select(ClauseSegment.clause_id, Segment.key)
            .join(Segment, Segment.id == ClauseSegment.segment_id)
            .where(ClauseSegment.clause_id.in_(segment_clause_ids))
            .order_by(Segment.key)
        )
        for clause_id, key in rows.tuples():
            segment_keys[clause_id].append(key)
    result: dict[uuid.UUID, list[ClauseOut]] = defaultdict(list)
    for c in clauses:
        rule_id = c.flag_rule_id if flag_rule_ids is not None else c.segment_rule_id
        assert rule_id is not None
        values = segment_keys[c.id] if c.operator == Operator.IN_SEGMENT else c.values
        result[rule_id].append(
            ClauseOut(attribute=c.attribute, operator=c.operator, values=values, negate=c.negate)
        )
    return dict(result)
