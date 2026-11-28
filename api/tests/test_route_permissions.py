"""Every `/api` route declares a `Require`, unless it is listed in PUBLIC_ROUTES."""

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from megooflags_api.auth.require import PUBLIC_ROUTES, Require
from megooflags_api.main import create_app


def _api_routes() -> list[RouteContext]:
    """The app's `/api` routes, with the dependencies of the routers that include them."""
    return [
        context
        for context in iter_route_contexts(create_app().routes)
        if isinstance(context.original_route, APIRoute) and (context.path or "").startswith("/api")
    ]


def _has_require(dependant: Dependant) -> bool:
    return any(isinstance(dep.call, Require) or _has_require(dep) for dep in dependant.dependencies)


def test_there_are_api_routes() -> None:
    assert len(_api_routes()) > 50


def test_every_api_route_requires_a_permission_or_is_public() -> None:
    unguarded = [
        (method, context.path)
        for context in _api_routes()
        for method in sorted(context.methods or ())
        if not _has_require(context.dependant) and (method, context.path) not in PUBLIC_ROUTES
    ]
    assert unguarded == []


def test_every_public_route_exists() -> None:
    routes = {(method, c.path) for c in _api_routes() for method in c.methods or ()}
    assert PUBLIC_ROUTES - routes == set()
