"""Variation values must match the flag type, which is fixed after creation."""

import math
from typing import Any

from megooflags_api.errors import validation_error
from megooflags_api.models.enums import FlagType


def check_value(flag_type: FlagType, value: Any, path: str) -> None:
    """Raise 422 `validation_error` at `path` unless `value` is valid for `flag_type`.

    `bool` is a subclass of `int` in Python, so it is excluded from numbers explicitly.
    JSON flags accept any JSON value, including null.
    """
    match flag_type:
        case FlagType.BOOLEAN:
            ok = isinstance(value, bool)
        case FlagType.STRING:
            ok = isinstance(value, str)
        case FlagType.NUMBER:
            ok = (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and math.isfinite(value)
            )
        case FlagType.JSON:
            ok = True
    if not ok:
        raise validation_error(f"value must be a {flag_type.value}", path)
