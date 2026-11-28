"""The fan-out job: turns outbox rows into one pending delivery per matching integration."""

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.models import (
    AuditEntry,
    Environment,
    Integration,
    IntegrationDelivery,
    IntegrationEnvironment,
    IntegrationOutbox,
    Project,
)
from megooflags_api.models.enums import DeliveryStatus
from megooflags_api.services.integrations.events import build_event, group_of
from megooflags_api.services.scheduler import JobResult

BATCH = 500


async def run_integration_fanout(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime, *, public_url: str | None
) -> JobResult:
    """Claims up to BATCH outbox rows, adds their deliveries and deletes the rows, in one
    transaction. Rows no integration wants are just deleted."""
    async with sessionmaker() as session:
        rows = list(
            await session.scalars(
                select(IntegrationOutbox)
                .order_by(IntegrationOutbox.id)
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        )
        if not rows:
            return JobResult()
        entries = {
            entry.id: (entry, project_key, env_key)
            for entry, project_key, env_key in await session.execute(
                select(AuditEntry, Project.key, Environment.key)
                .join(Project, Project.id == AuditEntry.project_id)
                .outerjoin(Environment, Environment.id == AuditEntry.environment_id)
                .where(AuditEntry.id.in_([row.audit_entry_id for row in rows]))
            )
        }
        integrations: dict[uuid.UUID, list[tuple[Integration, set[uuid.UUID]]]] = defaultdict(list)
        for integration, env_id in await session.execute(
            select(Integration, IntegrationEnvironment.environment_id)
            .outerjoin(
                IntegrationEnvironment, IntegrationEnvironment.integration_id == Integration.id
            )
            .where(
                Integration.project_id.in_({row.project_id for row in rows}),
                Integration.enabled,
            )
            .order_by(Integration.created_at, Integration.id)
        ):
            targets = integrations[integration.project_id]
            if not targets or targets[-1][0] is not integration:
                targets.append((integration, set()))
            if env_id is not None:
                targets[-1][1].add(env_id)

        deliveries: list[dict[str, Any]] = []
        for row in rows:
            found = entries.get(row.audit_entry_id)
            if found is None:
                continue  # the project is gone
            entry, project_key, env_key = found
            group = group_of(entry.action)
            if group is None:
                continue
            event = build_event(entry, project_key, env_key, public_url)
            for integration, env_ids in integrations.get(row.project_id, []):
                if group.value not in integration.event_groups:
                    continue
                if not (
                    integration.all_environments
                    or entry.environment_id is None
                    or entry.environment_id in env_ids
                ):
                    continue
                deliveries.append(
                    {
                        "integration_id": integration.id,
                        "audit_entry_id": entry.id,
                        "event_id": event["id"],
                        "event_type": entry.action,
                        "payload": event,
                        "status": DeliveryStatus.PENDING,
                        "next_attempt_at": now,
                    }
                )
        if deliveries:
            await session.execute(insert(IntegrationDelivery), deliveries)
        await session.execute(
            delete(IntegrationOutbox).where(IntegrationOutbox.id.in_([row.id for row in rows]))
        )
        await session.commit()
        return JobResult(ok=len(deliveries))
