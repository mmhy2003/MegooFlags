"""A flag's targeting config in one environment."""

import uuid
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import (
    Flag,
    FlagEnvironment,
    FlagPrerequisite,
    FlagRule,
    FlagTarget,
    RolloutWeight,
    Variation,
)
from megooflags_api.schemas.targeting import (
    FlagConfigOut,
    PrerequisiteIn,
    RolloutIn,
    RuleOut,
    Serve,
    TargetIn,
    WeightIn,
)
from megooflags_api.services.clauses import load_clauses


async def load_config(
    session: AsyncSession, flag: Flag, fe: FlagEnvironment, env_key: str
) -> FlagConfigOut:
    """The full config in six queries, however many targets, rules and clauses it has."""
    variation_keys: dict[uuid.UUID, str] = {
        id_: key
        for id_, key in (
            await session.execute(
                select(Variation.id, Variation.key)
                .where(Variation.flag_id == flag.id)
                .order_by(Variation.sort_order, Variation.key)
            )
        )
    }
    order = {id_: i for i, id_ in enumerate(variation_keys)}

    target_values: dict[uuid.UUID, list[str]] = defaultdict(list)
    for variation_id, context_key in await session.execute(
        select(FlagTarget.variation_id, FlagTarget.context_key)
        .where(FlagTarget.flag_environment_id == fe.id)
        .order_by(FlagTarget.context_key)
    ):
        target_values[variation_id].append(context_key)
    targets = [
        TargetIn(variation=variation_keys[variation_id], values=values)
        for variation_id, values in sorted(target_values.items(), key=lambda t: order[t[0]])
    ]

    rules = list(
        await session.scalars(
            select(FlagRule)
            .where(FlagRule.flag_environment_id == fe.id)
            .order_by(FlagRule.position)
        )
    )
    rule_ids = [rule.id for rule in rules]

    # Weights of the rule rollouts and of the fallthrough rollout, keyed by their owner's id.
    weights: dict[uuid.UUID, list[RolloutWeight]] = defaultdict(list)
    for weight in await session.scalars(
        select(RolloutWeight).where(
            or_(
                RolloutWeight.flag_rule_id.in_(rule_ids),
                RolloutWeight.flag_environment_id == fe.id,
            )
        )
    ):
        owner = weight.flag_rule_id or weight.flag_environment_id
        assert owner is not None
        weights[owner].append(weight)

    def serve(owner: uuid.UUID, variation_id: uuid.UUID | None, bucket_by: str | None) -> Serve:
        if variation_id is not None:
            return Serve(variation=variation_keys[variation_id])
        return Serve(
            rollout=RolloutIn(
                bucket_by=bucket_by or "key",
                weights=[
                    WeightIn(variation=variation_keys[w.variation_id], weight=w.weight)
                    for w in sorted(weights[owner], key=lambda w: order[w.variation_id])
                ],
            )
        )

    clauses = await load_clauses(session, flag_rule_ids=rule_ids)

    prerequisites = [
        PrerequisiteIn(flag=flag_key, variation=variation_key)
        for flag_key, variation_key in (
            await session.execute(
                select(Flag.key, Variation.key)
                .select_from(FlagPrerequisite)
                .join(Flag, Flag.id == FlagPrerequisite.prerequisite_flag_id)
                .join(Variation, Variation.id == FlagPrerequisite.variation_id)
                .where(FlagPrerequisite.flag_environment_id == fe.id)
                .order_by(FlagPrerequisite.position)
            )
        )
    ]

    return FlagConfigOut(
        environment=env_key,
        enabled=fe.enabled,
        version=fe.version,
        targets=targets,
        rules=[
            RuleOut(
                **serve(rule.id, rule.variation_id, rule.bucket_by).model_dump(),
                description=rule.description,
                clauses=clauses.get(rule.id, []),
            )
            for rule in rules
        ],
        fallthrough=serve(fe.id, fe.fallthrough_variation_id, fe.fallthrough_bucket_by),
        prerequisites=prerequisites,
        updated_at=fe.updated_at,
    )
