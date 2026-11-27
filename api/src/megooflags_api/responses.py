"""JSON responses for the SDK and OFREP routes, encoded with orjson."""

import json
from typing import Any

import orjson
from fastapi.responses import JSONResponse


def dumps(content: Any) -> bytes:
    """orjson bytes, or the standard encoder for what orjson refuses (integers beyond 64 bits)."""
    try:
        return orjson.dumps(content)
    except TypeError:  # orjson.JSONEncodeError is a TypeError
        return json.dumps(
            content, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()


class FastJSONResponse(JSONResponse):
    """A JSONResponse rendered with `dumps`."""

    def render(self, content: Any) -> bytes:
        return dumps(content)
