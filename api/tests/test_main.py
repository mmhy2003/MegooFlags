from typing import Any

import pytest
from starlette.routing import BaseRoute, Match

from megooflags_api.main import create_app


def _scope(method: str, path: str) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }


def _first_full_match(routes: list[BaseRoute], method: str, path: str) -> int:
    for i, route in enumerate(routes):
        match, _ = route.matches(_scope(method, path))
        if match == Match.FULL:
            return i
    raise AssertionError(f"no route matches {method} {path}")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/ofrep/v1/evaluate/flags/flag-1"),
        ("POST", "/ofrep/v1/evaluate/flags"),
        ("POST", "/sdk/v1/evaluate"),
        ("GET", "/sdk/v1/ruleset"),
        ("GET", "/sdk/v1/stream"),
    ],
)
def test_sdk_and_ofrep_routes_match_before_the_api_routes(method: str, path: str) -> None:
    routes = create_app().router.routes
    first_api = _first_full_match(routes, "GET", "/api/projects")

    assert _first_full_match(routes, method, path) < first_api
