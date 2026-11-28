"""The instance-wide settings: the singleton `org_settings` row (id 1)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import OrgSettings
from megooflags_api.schemas.auth import OrgSettingsIn, OrgSettingsOut


async def get_org_settings(session: AsyncSession, *, for_update: bool = False) -> OrgSettings:
    statement = select(OrgSettings).where(OrgSettings.id == 1)
    if for_update:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    # Seeded by migration 0004 and never deleted.
    assert row is not None, "the org_settings row is missing"
    return row


async def require_mfa(session: AsyncSession) -> bool:
    return bool(await session.scalar(select(OrgSettings.require_mfa).where(OrgSettings.id == 1)))


async def update_org_settings(session: AsyncSession, row: OrgSettings, body: OrgSettingsIn) -> None:
    """Apply the change to the row (read `for_update`) and flush. The caller audits and commits."""
    row.require_mfa = body.require_mfa
    await session.flush()


def org_settings_out(row: OrgSettings) -> OrgSettingsOut:
    return OrgSettingsOut(require_mfa=row.require_mfa)
