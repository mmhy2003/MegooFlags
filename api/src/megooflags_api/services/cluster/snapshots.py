"""Published environment snapshots and their cluster-wide versions.

A build locks the environment's `environment_snapshot` row before it reads the ruleset and the
keys, so builds of one environment run one at a time and a higher version never carries older
content. The version changes only when the content digest does.
"""

import uuid
from dataclasses import replace

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import Environment, EnvironmentSnapshot, Project, SdkKey
from megooflags_api.models.cluster import SNAPSHOT_VERSION_SEQ
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import (
    FORMAT,
    SignedSnapshot,
    SnapshotHeader,
    SnapshotKey,
    content_digest,
)
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import RulesetCache

FOREIGN_KEY_VIOLATION = "23503"


async def environment_refs(
    session: AsyncSession, project_id: uuid.UUID | None = None
) -> list[tuple[EnvRef, str]]:
    """Every environment (of one project) with its project key, ordered by id."""
    query = (
        select(Environment.id, Environment.project_id, Environment.key, Project.key)
        .join(Project, Project.id == Environment.project_id)
        .order_by(Environment.id)
    )
    if project_id is not None:
        query = query.where(Environment.project_id == project_id)
    rows = await session.execute(query)
    return [
        (EnvRef(id=id, project_id=project, key=key), project_key)
        for id, project, key, project_key in rows
    ]


async def build_snapshot(
    session: AsyncSession,
    environment: EnvRef,
    project_key: str,
    cache: RulesetCache,
    signer: SnapshotSigner,
) -> SignedSnapshot | None:
    """None when the environment no longer exists. Commits."""
    try:
        await session.execute(
            insert(EnvironmentSnapshot)
            .values(environment_id=environment.id, version=0, digest="", published_at=func.now())
            .on_conflict_do_nothing(index_elements=[EnvironmentSnapshot.environment_id])
        )
    except IntegrityError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) == FOREIGN_KEY_VIOLATION:
            return None
        raise
    row = (
        await session.execute(
            select(
                EnvironmentSnapshot.version,
                EnvironmentSnapshot.digest,
                EnvironmentSnapshot.published_at,
            )
            .where(EnvironmentSnapshot.environment_id == environment.id)
            .with_for_update()
        )
    ).one_or_none()
    if row is None:  # the environment was deleted since the insert
        await session.rollback()
        return None
    entry = await cache.get_entry(session, environment, max_age=0)
    keys = await session.execute(
        select(
            SdkKey.id, SdkKey.kind, SdkKey.secret_hash, SdkKey.revoked_at, SdkKey.expires_at
        ).where(SdkKey.environment_id == environment.id)
    )
    header = SnapshotHeader(
        format=FORMAT,
        environment_id=environment.id,
        environment_key=environment.key,
        project_id=environment.project_id,
        project_key=project_key,
        version=row.version,
        published_at=row.published_at,
        keys=tuple(SnapshotKey(*key) for key in keys),
    )
    digest = content_digest(header, entry.body)
    if digest != row.digest:
        published = (
            await session.execute(
                update(EnvironmentSnapshot)
                .where(EnvironmentSnapshot.environment_id == environment.id)
                .values(
                    version=SNAPSHOT_VERSION_SEQ.next_value(),
                    digest=digest,
                    published_at=func.now(),
                )
                .returning(EnvironmentSnapshot.version, EnvironmentSnapshot.published_at)
            )
        ).one()
        header = replace(header, version=published.version, published_at=published.published_at)
    await session.commit()
    unsigned = SignedSnapshot(header=header, ruleset=entry.body, signature="")
    return replace(unsigned, signature=signer.sign(unsigned.payload()))


async def current_snapshot_version(session: AsyncSession) -> int:
    """The newest snapshot version of any environment, or 0."""
    version: int | None = await session.scalar(select(func.max(EnvironmentSnapshot.version)))
    return version or 0
