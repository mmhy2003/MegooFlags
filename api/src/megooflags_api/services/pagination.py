"""Keyset pagination on `key`: queries fetch `limit + 1` rows after the cursor key."""

import base64
import binascii
from collections.abc import Callable

from megooflags_api.errors import validation_error


def encode_cursor(key: str) -> str:
    return base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        key = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise validation_error("invalid cursor", "cursor") from None
    if not key:
        raise validation_error("invalid cursor", "cursor")
    return key


def page_slice[T](
    rows: list[T], limit: int, key_of: Callable[[T], str]
) -> tuple[list[T], str | None]:
    """Cut the extra row a query fetched and return the cursor for the next page, if any."""
    if len(rows) <= limit:
        return rows, None
    kept = rows[:limit]
    return kept, encode_cursor(key_of(kept[-1]))
