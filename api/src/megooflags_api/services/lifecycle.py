"""Flag lifecycle: the state a flag shows and the permanent mark."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import Flag
from megooflags_api.models.enums import FlagLifecycle
from megooflags_api.schemas.flags import LifecycleState


def lifecycle_state(archived_at: datetime | None, lifecycle: FlagLifecycle) -> LifecycleState:
    """An archived flag shows "archived" whatever its stored state."""
    return "archived" if archived_at is not None else lifecycle.value


async def set_permanent(session: AsyncSession, flag: Flag, permanent: bool) -> None:
    """Marking sets permanent; clearing the mark gives active if the flag was ever evaluated,
    else new. Clearing a flag that is not permanent changes nothing."""
    if permanent:
        flag.lifecycle = FlagLifecycle.PERMANENT
    elif flag.lifecycle == FlagLifecycle.PERMANENT:
        flag.lifecycle = (
            FlagLifecycle.ACTIVE if flag.last_evaluated_at is not None else FlagLifecycle.NEW
        )
    await session.flush()
