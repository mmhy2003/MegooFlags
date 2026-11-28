"""The ruleset of one environment: every non-archived flag and segment, as SDKs download it.

The output follows spec/ruleset.schema.json and holds plain JSON types only. It is built in a
fixed number of statements whatever the number of flags: each child query joins on the
environment or the project instead of passing ids, and every query runs even when it is empty.
"""

import uuid
from collections import defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import (
    Clause,
    ClauseSegment,
    Environment,
    Flag,
    FlagEnvironment,
    FlagPrerequisite,
    FlagRule,
    FlagTarget,
    Project,
    RolloutWeight,
    Segment,
    SegmentRule,
    SegmentTarget,
    Variation,
)
from megooflags_api.models.enums import Operator, SegmentTargetKind

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class EnvRef:
    """The environment columns the SDK path needs, detached from any session."""

    id: uuid.UUID
    project_id: uuid.UUID
    key: str

    @classmethod
    def of(cls, environment: Environment) -> "EnvRef":
        return cls(id=environment.id, project_id=environment.project_id, key=environment.key)


def _clause(attribute: str, operator: Operator, values: list[Any], negate: bool) -> dict[str, Any]:
    return {"attribute": attribute, "operator": operator.value, "values": values, "negate": negate}


@dataclass(frozen=True, slots=True)
class FlagParts:
    """Flags as SDKs download them, ordered by key as in build_ruleset, with ids and versions."""

    flags: dict[str, dict[str, Any]]
    ids: dict[uuid.UUID, str]
    versions: dict[uuid.UUID, int]


@dataclass(frozen=True, slots=True)
class RulesetParts:
    """A whole ruleset before it is joined into one document."""

    project: str
    environment: str
    flags: FlagParts
    segments: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        """The ruleset document of build_ruleset."""
        return {
            "schema_version": SCHEMA_VERSION,
            "project": self.project,
            "environment": self.environment,
            "flags": self.flags.flags,
            "segments": self.segments,
        }


async def build_flags(
    session: AsyncSession, environment: EnvRef, flag_ids: Collection[uuid.UUID] | None = None
) -> FlagParts:
    """The flags of `environment`, or only `flag_ids`, in 9 statements."""
    in_env = FlagEnvironment.environment_id == environment.id
    live_flag = (Flag.project_id == environment.project_id) & Flag.archived_at.is_(None)
    wanted_flag: ColumnElement[bool] = true()
    wanted_variation: ColumnElement[bool] = true()
    wanted_fe: ColumnElement[bool] = true()
    if flag_ids is not None:
        wanted_flag = Flag.id.in_(flag_ids)
        wanted_variation = Variation.flag_id.in_(flag_ids)
        wanted_fe = FlagEnvironment.flag_id.in_(flag_ids)

    flag_rows = (
        await session.execute(
            select(Flag.id, Flag.key, Flag.type, Flag.off_variation_id)
            .where(live_flag, wanted_flag)
            .order_by(Flag.key)
        )
    ).all()

    # Variation keys by id, and each variation's rank within its flag.
    variation_keys: dict[uuid.UUID, str] = {}
    order: dict[uuid.UUID, int] = {}
    variations: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for id_, flag_id, key, value in await session.execute(
        select(Variation.id, Variation.flag_id, Variation.key, Variation.value)
        .join(Flag, Flag.id == Variation.flag_id)
        .where(live_flag, wanted_variation)
        .order_by(Variation.flag_id, Variation.sort_order, Variation.key)
    ):
        variation_keys[id_] = key
        order[id_] = len(variations[flag_id])
        variations[flag_id].append({"key": key, "value": value})

    fes = {
        row.flag_id: row
        for row in await session.execute(
            select(
                FlagEnvironment.id,
                FlagEnvironment.flag_id,
                FlagEnvironment.enabled,
                FlagEnvironment.version,
                FlagEnvironment.fallthrough_variation_id,
                FlagEnvironment.fallthrough_bucket_by,
            )
            .join(Flag, Flag.id == FlagEnvironment.flag_id)
            .where(in_env, wanted_fe, live_flag)
        )
    }

    # Context keys per flag and variation, already sorted.
    target_values: dict[uuid.UUID, dict[uuid.UUID, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for flag_id, variation_id, context_key in await session.execute(
        select(FlagEnvironment.flag_id, FlagTarget.variation_id, FlagTarget.context_key)
        .join(FlagEnvironment, FlagEnvironment.id == FlagTarget.flag_environment_id)
        .where(in_env, wanted_fe)
        .order_by(FlagTarget.context_key)
    ):
        target_values[flag_id][variation_id].append(context_key)

    rules: dict[uuid.UUID, list[Any]] = defaultdict(list)
    for rule in await session.execute(
        select(
            FlagRule.id,
            FlagEnvironment.flag_id,
            FlagRule.description,
            FlagRule.variation_id,
            FlagRule.bucket_by,
        )
        .join(FlagEnvironment, FlagEnvironment.id == FlagRule.flag_environment_id)
        .where(in_env, wanted_fe)
        .order_by(FlagRule.flag_environment_id, FlagRule.position)
    ):
        rules[rule.flag_id].append(rule)

    # Weights of rule rollouts and of fallthrough rollouts, keyed by their owner's id.
    weights: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = defaultdict(list)
    for owner, variation_id, weight in await session.execute(
        select(
            func.coalesce(RolloutWeight.flag_rule_id, RolloutWeight.flag_environment_id),
            RolloutWeight.variation_id,
            RolloutWeight.weight,
        )
        .outerjoin(FlagRule, FlagRule.id == RolloutWeight.flag_rule_id)
        .join(
            FlagEnvironment,
            FlagEnvironment.id
            == func.coalesce(FlagRule.flag_environment_id, RolloutWeight.flag_environment_id),
        )
        .where(in_env, wanted_fe)
    ):
        weights[owner].append((variation_id, weight))

    clause_rows = (
        await session.execute(
            select(
                Clause.id,
                Clause.flag_rule_id,
                Clause.attribute,
                Clause.operator,
                Clause.values,
                Clause.negate,
            )
            .join(FlagRule, FlagRule.id == Clause.flag_rule_id)
            .join(FlagEnvironment, FlagEnvironment.id == FlagRule.flag_environment_id)
            .where(in_env, wanted_fe)
            .order_by(Clause.flag_rule_id, Clause.position)
        )
    ).all()

    segment_keys: dict[uuid.UUID, list[str]] = defaultdict(list)
    for clause_id, segment_key in await session.execute(
        select(ClauseSegment.clause_id, Segment.key)
        .join(Segment, Segment.id == ClauseSegment.segment_id)
        .join(Clause, Clause.id == ClauseSegment.clause_id)
        .join(FlagRule, FlagRule.id == Clause.flag_rule_id)
        .join(FlagEnvironment, FlagEnvironment.id == FlagRule.flag_environment_id)
        .where(in_env, wanted_fe)
        .order_by(Segment.key)
    ):
        segment_keys[clause_id].append(segment_key)

    clauses: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for c in clause_rows:
        in_segment = c.operator == Operator.IN_SEGMENT
        values = segment_keys[c.id] if in_segment else list(c.values)
        clauses[c.flag_rule_id].append(_clause(c.attribute, c.operator, values, c.negate))

    prerequisites: dict[uuid.UUID, list[dict[str, str]]] = defaultdict(list)
    for flag_id, prerequisite_key, variation_key in await session.execute(
        select(FlagEnvironment.flag_id, Flag.key, Variation.key)
        .select_from(FlagPrerequisite)
        .join(FlagEnvironment, FlagEnvironment.id == FlagPrerequisite.flag_environment_id)
        .join(Flag, Flag.id == FlagPrerequisite.prerequisite_flag_id)
        .join(Variation, Variation.id == FlagPrerequisite.variation_id)
        .where(in_env, wanted_fe)
        .order_by(FlagPrerequisite.position)
    ):
        prerequisites[flag_id].append({"flag": prerequisite_key, "variation": variation_key})

    def serve(owner: uuid.UUID, variation_id: uuid.UUID | None, bucket_by: str | None) -> Any:
        if variation_id is not None:
            return {"variation": variation_keys[variation_id]}
        return {
            "rollout": {
                "bucket_by": bucket_by or "key",
                "weights": [
                    {"variation": variation_keys[v], "weight": w}
                    for v, w in sorted(weights[owner], key=lambda vw: order[vw[0]])
                ],
            }
        }

    flags: dict[str, dict[str, Any]] = {}
    ids: dict[uuid.UUID, str] = {}
    versions: dict[uuid.UUID, int] = {}
    for flag in flag_rows:
        fe = fes[flag.id]
        ids[flag.id] = flag.key
        versions[flag.id] = fe.version
        targets = target_values[flag.id]
        flags[flag.key] = {
            "key": flag.key,
            "type": flag.type.value,
            "enabled": fe.enabled,
            "version": fe.version,
            "variations": variations[flag.id],
            "off_variation": (
                None if flag.off_variation_id is None else variation_keys[flag.off_variation_id]
            ),
            "prerequisites": prerequisites[flag.id],
            "targets": [
                {"variation": variation_keys[variation_id], "values": targets[variation_id]}
                for variation_id in sorted(targets, key=order.__getitem__)
            ],
            "rules": [
                {
                    "description": rule.description,
                    "clauses": clauses[rule.id],
                    "serve": serve(rule.id, rule.variation_id, rule.bucket_by),
                }
                for rule in rules[flag.id]
            ],
            "fallthrough": serve(fe.id, fe.fallthrough_variation_id, fe.fallthrough_bucket_by),
        }

    return FlagParts(flags=flags, ids=ids, versions=versions)


async def build_segments(session: AsyncSession, environment: EnvRef) -> dict[str, Any]:
    """The segments section in 4 statements."""
    project_id = environment.project_id
    segment_rows = (
        await session.execute(
            select(Segment.id, Segment.key)
            .where(Segment.project_id == project_id)
            .order_by(Segment.key)
        )
    ).all()
    segment_targets: dict[tuple[uuid.UUID, SegmentTargetKind], list[str]] = defaultdict(list)
    for segment_id, context_key, kind in await session.execute(
        select(SegmentTarget.segment_id, SegmentTarget.context_key, SegmentTarget.kind)
        .join(Segment, Segment.id == SegmentTarget.segment_id)
        .where(Segment.project_id == project_id)
        .order_by(SegmentTarget.context_key)
    ):
        segment_targets[segment_id, kind].append(context_key)
    segment_rules: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for rule_id, segment_id in await session.execute(
        select(SegmentRule.id, SegmentRule.segment_id)
        .join(Segment, Segment.id == SegmentRule.segment_id)
        .where(Segment.project_id == project_id)
        .order_by(SegmentRule.segment_id, SegmentRule.position)
    ):
        segment_rules[segment_id].append(rule_id)
    segment_clauses: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for c in await session.execute(
        select(
            Clause.segment_rule_id, Clause.attribute, Clause.operator, Clause.values, Clause.negate
        )
        .join(SegmentRule, SegmentRule.id == Clause.segment_rule_id)
        .join(Segment, Segment.id == SegmentRule.segment_id)
        .where(Segment.project_id == project_id)
        .order_by(Clause.segment_rule_id, Clause.position)
    ):
        segment_clauses[c.segment_rule_id].append(
            _clause(c.attribute, c.operator, list(c.values), c.negate)
        )

    segments = {
        key: {
            "included": segment_targets[id_, SegmentTargetKind.INCLUDED],
            "excluded": segment_targets[id_, SegmentTargetKind.EXCLUDED],
            "rules": [{"clauses": segment_clauses[rule_id]} for rule_id in segment_rules[id_]],
        }
        for id_, key in segment_rows
    }

    return segments


async def build_parts(session: AsyncSession, environment: EnvRef) -> RulesetParts:
    """The ruleset of `environment` in 14 statements."""
    project_key = await session.scalar(
        select(Project.key).where(Project.id == environment.project_id)
    )
    assert project_key is not None
    flags = await build_flags(session, environment)
    segments = await build_segments(session, environment)
    return RulesetParts(
        project=project_key, environment=environment.key, flags=flags, segments=segments
    )


async def build_ruleset(session: AsyncSession, environment: EnvRef) -> dict[str, Any]:
    """The ruleset of `environment` in 14 statements."""
    return (await build_parts(session, environment)).to_json()


async def flag_versions(session: AsyncSession, environment: EnvRef) -> dict[uuid.UUID, int]:
    """flag_environment.version per live flag of `environment`, in 1 statement."""
    rows = await session.execute(
        select(FlagEnvironment.flag_id, FlagEnvironment.version)
        .join(Flag, Flag.id == FlagEnvironment.flag_id)
        .where(
            FlagEnvironment.environment_id == environment.id,
            Flag.project_id == environment.project_id,
            Flag.archived_at.is_(None),
        )
    )
    return {flag_id: version for flag_id, version in rows}
