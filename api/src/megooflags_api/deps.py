from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    id: str


async def get_actor() -> Actor | None:
    """No authentication yet: the management API is open. The governance task plugs auth in here."""
    return None
