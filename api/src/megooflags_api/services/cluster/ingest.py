"""Event batches forwarded by workers, stored once per batch id, and the retention of those ids.

A worker resends a batch with the same id when it does not know whether the control plane took
it, so the id is recorded in the same transaction as the events.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.models import Environment, NodeEventBatch
from megooflags_api.schemas.cluster import ClusterEventsIn
from megooflags_api.services.events import add_counts, record_exposures, sum_counts
from megooflags_api.services.scheduler import JobResult

BATCH_RETENTION = timedelta(days=7)


async def ingest_batch(
    session: AsyncSession,
    node_id: uuid.UUID,
    body: ClusterEventsIn,
    *,
    persist_context: bool,
    now: datetime,
) -> bool:
    """Store the batch and commit; False when its id was seen before. The data of an
    environment that no longer exists is dropped, but the id is kept."""
    stored = await session.scalar(
        insert(NodeEventBatch)
        .values(batch_id=body.batch_id, node_id=node_id)
        .on_conflict_do_nothing(index_elements=["batch_id"])
        .returning(NodeEventBatch.batch_id)
    )
    if stored is None:
        return False
    # KEY SHARE keeps the environment from being deleted until the events are in.
    environment_id = await session.scalar(
        select(Environment.id)
        .where(Environment.id == body.environment_id)
        .with_for_update(key_share=True)
    )
    if environment_id is not None:
        await record_exposures(
            session, environment_id, body.exposures, persist_context=persist_context, now=now
        )
        await add_counts(session, sum_counts(environment_id, body.counts))
    await session.commit()
    return True


async def delete_old_batches(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime
) -> JobResult:
    """Forget batch ids received more than 7 days ago."""
    async with sessionmaker() as session:
        result = await session.execute(
            delete(NodeEventBatch).where(NodeEventBatch.received_at < now - BATCH_RETENTION)
        )
        await session.commit()
    return JobResult(ok=int(result.rowcount))  # type: ignore[attr-defined]
