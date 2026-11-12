"""A flag's targeting config in one environment."""

import uuid
from collections import defaultdict

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import invalid_reference, validation_error, version_conflict
from megooflags_api.models import (
    Environment,
    Flag,
    FlagEnvironment,
    FlagPrerequisite,
    FlagRule,
    FlagTarget,
    RolloutWeight,
    Variation,
)
from megooflags_api.schemas.common import FlagEnvironmentState
from megooflags_api.schemas.targeting import (
    ClauseIn,
    FlagConfigIn,
    FlagConfigOut,
    PrerequisiteIn,
    RolloutIn,
    RuleOut,
    Serve,
    TargetIn,
    WeightIn,
)
from megooflags_api.services.clauses import (
    check_clause,
    insert_clauses,
    load_clauses,
    resolve_segments,
)
from megooflags_api.services.lookup import get_flag_environment
from megooflags_api.services.prerequisites import check_prerequisites, lock_prerequisite_graph


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


async def set_enabled(session: AsyncSession, fe: FlagEnvironment, enabled: bool) -> None:
    """Turn `fe` on or off; the version only moves when the state changes."""
    if fe.enabled == enabled:
        return
    fe.enabled = enabled
    fe.version = fe.version + 1
    fe.updated_at = func.now()
    await session.flush()
    await session.refresh(fe)  # load the updated_at the database set


def state_of(fe: FlagEnvironment, env_key: str) -> FlagEnvironmentState:
    """`fe`'s on/off state and version, as the toggle and kill switch return it."""
    return FlagEnvironmentState(
        environment=env_key, enabled=fe.enabled, version=fe.version, updated_at=fe.updated_at
    )


TOTAL_WEIGHT = 100000


def _check_targets(body: FlagConfigIn) -> None:
    seen: set[str] = set()
    for i, target in enumerate(body.targets):
        for j, key in enumerate(target.values):
            if key in seen:
                raise validation_error(
                    f"context key '{key}' is in more than one target list",
                    f"targets[{i}].values[{j}]",
                )
            seen.add(key)


def _check_weights(weights: list[WeightIn], path: str) -> None:
    seen: set[str] = set()
    for j, weight in enumerate(weights):
        if weight.variation in seen:
            raise validation_error(
                f"variation '{weight.variation}' is listed twice", f"{path}[{j}].variation"
            )
        seen.add(weight.variation)
    if sum(w.weight for w in weights) != TOTAL_WEIGHT:
        raise validation_error(f"weights must add up to {TOTAL_WEIGHT}", path)


def _check_rollout(rollout: RolloutIn, path: str) -> None:
    _check_weights(rollout.weights, f"{path}.weights")


async def check_fallthrough_weights(
    session: AsyncSession, flag: Flag, weights: list[WeightIn]
) -> dict[str, uuid.UUID]:
    """Validate the `weights` of a fallthrough rollout; returns the variation ids by key."""
    variation_ids: dict[str, uuid.UUID] = {
        key: id_
        for key, id_ in await session.execute(
            select(Variation.key, Variation.id).where(Variation.flag_id == flag.id)
        )
    }
    for j, weight in enumerate(weights):
        if weight.variation not in variation_ids:
            raise invalid_reference(
                f"unknown variation '{weight.variation}'", f"weights[{j}].variation"
            )
    _check_weights(weights, "weights")
    return variation_ids


async def set_fallthrough_rollout(
    session: AsyncSession,
    flag: Flag,
    fe: FlagEnvironment,
    weights: list[WeightIn],
    *,
    bucket_by: str | None = None,
) -> None:
    """Serve a rollout of `weights` as the fallthrough of `fe`, which the caller has locked.

    `bucket_by` defaults to the current one, or "key"; targets and rules are left as they are.
    """
    variation_ids = await check_fallthrough_weights(session, flag, weights)
    await session.execute(delete(RolloutWeight).where(RolloutWeight.flag_environment_id == fe.id))
    await session.execute(
        insert(RolloutWeight),
        _weight_rows(RolloutIn(weights=weights), variation_ids, flag_environment_id=fe.id),
    )
    fe.fallthrough_variation_id = None
    fe.fallthrough_bucket_by = bucket_by or fe.fallthrough_bucket_by or "key"
    fe.version = fe.version + 1
    fe.updated_at = func.now()
    await session.flush()
    await session.refresh(fe)  # load the updated_at the database set


async def set_fallthrough_variation(
    session: AsyncSession, fe: FlagEnvironment, variation_id: uuid.UUID
) -> None:
    """Serve `variation_id` as the fallthrough of `fe`, which the caller has locked."""
    await session.execute(delete(RolloutWeight).where(RolloutWeight.flag_environment_id == fe.id))
    fe.fallthrough_variation_id = variation_id
    fe.fallthrough_bucket_by = None
    fe.version = fe.version + 1
    fe.updated_at = func.now()
    await session.flush()
    await session.refresh(fe)  # load the updated_at the database set


async def fallthrough_weights(session: AsyncSession, fe: FlagEnvironment) -> dict[str, int] | None:
    """The fallthrough rollout of `fe` as weights by variation key; None for a fixed variation."""
    if fe.fallthrough_variation_id is not None:
        return None
    return {
        key: weight
        for key, weight in await session.execute(
            select(Variation.key, RolloutWeight.weight)
            .join(Variation, Variation.id == RolloutWeight.variation_id)
            .where(RolloutWeight.flag_environment_id == fe.id)
        )
    }


def _serves(body: FlagConfigIn) -> list[tuple[str, Serve]]:
    """Every rule outcome and the fallthrough, with their paths."""
    return [(f"rules[{i}]", rule) for i, rule in enumerate(body.rules)] + [
        ("fallthrough", body.fallthrough)
    ]


def _variation_refs(body: FlagConfigIn) -> list[tuple[str, str]]:
    """Every variation key the body references, with its path, in body order."""
    refs = [(f"targets[{i}].variation", t.variation) for i, t in enumerate(body.targets)]
    for path, serve in _serves(body):
        if serve.variation is not None:
            refs.append((f"{path}.variation", serve.variation))
        if serve.rollout is not None:
            refs.extend(
                (f"{path}.rollout.weights[{j}].variation", w.variation)
                for j, w in enumerate(serve.rollout.weights)
            )
    return refs


def _weight_rows(
    rollout: RolloutIn, ids: dict[str, uuid.UUID], **owner: uuid.UUID
) -> list[dict[str, object]]:
    return [
        {"id": uuid.uuid4(), "variation_id": ids[w.variation], "weight": w.weight, **owner}
        for w in rollout.weights
    ]


async def replace_config(
    session: AsyncSession,
    flag: Flag,
    fe: FlagEnvironment,
    body: FlagConfigIn,
    *,
    check_version: bool = True,
    set_enabled: bool = True,
) -> None:
    """Replace the whole config of `fe`, which the caller has locked (SELECT ... FOR UPDATE).

    Everything is validated before the first delete, so a refused body leaves the config as is.
    """
    if check_version and body.version != fe.version:
        raise version_conflict(fe.version)

    variation_ids: dict[str, uuid.UUID] = {
        key: id_
        for key, id_ in await session.execute(
            select(Variation.key, Variation.id).where(Variation.flag_id == flag.id)
        )
    }
    for path, key in _variation_refs(body):
        if key not in variation_ids:
            raise invalid_reference(f"unknown variation '{key}'", path)

    _check_targets(body)
    clauses_by_path: list[tuple[str, ClauseIn]] = []
    for i, rule in enumerate(body.rules):
        for k, clause in enumerate(rule.clauses):
            path = f"rules[{i}].clauses[{k}]"
            check_clause(clause, path, allow_segments=True)
            clauses_by_path.append((path, clause))
    for path, serve in _serves(body):
        if serve.rollout is not None:
            _check_rollout(serve.rollout, f"{path}.rollout")
    segment_ids = await resolve_segments(session, flag.project_id, clauses_by_path)
    prerequisites = await check_prerequisites(session, flag, fe.environment_id, body.prerequisites)

    await session.execute(delete(FlagTarget).where(FlagTarget.flag_environment_id == fe.id))
    await session.execute(delete(FlagRule).where(FlagRule.flag_environment_id == fe.id))
    await session.execute(delete(RolloutWeight).where(RolloutWeight.flag_environment_id == fe.id))
    await session.execute(
        delete(FlagPrerequisite).where(FlagPrerequisite.flag_environment_id == fe.id)
    )

    targets = [
        {
            "id": uuid.uuid4(),
            "flag_environment_id": fe.id,
            "variation_id": variation_ids[target.variation],
            "context_key": key,
        }
        for target in body.targets
        for key in target.values
    ]
    if targets:
        await session.execute(insert(FlagTarget), targets)

    rule_ids = [uuid.uuid4() for _ in body.rules]
    if body.rules:
        await session.execute(
            insert(FlagRule),
            [
                {
                    "id": rule_id,
                    "flag_environment_id": fe.id,
                    "position": position,
                    "description": rule.description,
                    "variation_id": None
                    if rule.variation is None
                    else variation_ids[rule.variation],
                    "bucket_by": None if rule.rollout is None else rule.rollout.bucket_by,
                }
                for position, (rule_id, rule) in enumerate(zip(rule_ids, body.rules, strict=True))
            ],
        )
    weights: list[dict[str, object]] = []
    for rule_id, rule in zip(rule_ids, body.rules, strict=True):
        if rule.rollout is not None:
            weights += _weight_rows(rule.rollout, variation_ids, flag_rule_id=rule_id)
    fallthrough = body.fallthrough
    if fallthrough.rollout is not None:
        weights += _weight_rows(fallthrough.rollout, variation_ids, flag_environment_id=fe.id)
    if weights:
        await session.execute(insert(RolloutWeight), weights)
    for rule_id, rule in zip(rule_ids, body.rules, strict=True):
        await insert_clauses(
            session,
            rule.clauses,
            flag_rule_id=rule_id,
            segment_rule_id=None,
            segment_ids=segment_ids,
        )
    if prerequisites:
        await session.execute(
            insert(FlagPrerequisite),
            [
                {
                    "id": uuid.uuid4(),
                    "flag_environment_id": fe.id,
                    "prerequisite_flag_id": prerequisite_flag_id,
                    "variation_id": variation_id,
                    "position": position,
                }
                for position, (prerequisite_flag_id, variation_id) in enumerate(prerequisites)
            ],
        )

    if fallthrough.variation is not None:
        fe.fallthrough_variation_id = variation_ids[fallthrough.variation]
        fe.fallthrough_bucket_by = None
    else:
        assert fallthrough.rollout is not None
        fe.fallthrough_variation_id = None
        fe.fallthrough_bucket_by = fallthrough.rollout.bucket_by
    if set_enabled:
        fe.enabled = body.enabled
    fe.version = fe.version + 1
    fe.updated_at = func.now()
    await session.flush()
    await session.refresh(fe)  # load the updated_at the database set


async def copy_config(
    session: AsyncSession,
    flag: Flag,
    source: Environment,
    targets: list[Environment],
    include_enabled: bool,
) -> None:
    """Replace the config of `flag` in every target environment with its config in `source`.

    The on/off state is copied only with `include_enabled`. Each target is validated against its
    own environment (prerequisite cycles), so the caller must roll back if one is refused.
    """
    target_ids = [env.id for env in targets]
    if len(set(target_ids)) != len(target_ids):
        raise validation_error("an environment is listed twice", "to")
    if source.id in target_ids:
        raise validation_error(f"cannot copy environment '{source.key}' to itself", "to")

    config = await load_config(
        session, flag, await get_flag_environment(session, flag, source), source.key
    )
    if config.prerequisites:
        # All targets up front, in order: one by one in replace_config could deadlock two copies.
        await lock_prerequisite_graph(session, target_ids)
    rows = await session.scalars(
        select(FlagEnvironment)
        .where(FlagEnvironment.flag_id == flag.id, FlagEnvironment.environment_id.in_(target_ids))
        .order_by(FlagEnvironment.id)
        .with_for_update()
    )
    for fe in rows.all():
        body = FlagConfigIn.model_validate(
            config.model_dump(include={"targets", "rules", "fallthrough", "prerequisites"})
            | {"version": fe.version, "enabled": config.enabled}
        )
        await replace_config(
            session, flag, fe, body, check_version=False, set_enabled=include_enabled
        )
