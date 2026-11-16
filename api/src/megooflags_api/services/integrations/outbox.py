"""The integration outbox: audit entries waiting for the fan-out job, written in the change's
own transaction, so a rolled-back change sends nothing and a committed one is never missed."""

from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import AuditEntry, IntegrationOutbox
from megooflags_api.services.integrations.events import group_of


async def enqueue(session: AsyncSession, entry: AuditEntry) -> None:
    """Adds an outbox row for a flushed `entry` of a project whose action is in a group."""
    if entry.project_id is not None and group_of(entry.action) is not None:
        session.add(IntegrationOutbox(audit_entry_id=entry.id, project_id=entry.project_id))
