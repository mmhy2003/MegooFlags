"""`require(permission)`: the dependency every `/api` route declares.

It resolves the caller, loads their authorizer (once per request), resolves `{project}` and
`{env}` from the path (404 when unknown or hidden) and checks the permission (403).
"""

import uuid
from dataclasses import dataclass

from fastapi import Request

from megooflags_api.auth.principal import (
    Principal,
    PrincipalKind,
    check_session_state,
    resolve_principal,
)
from megooflags_api.auth.rbac import Authorizer, Permission, load_authorizer
from megooflags_api.db import SessionDep
from megooflags_api.errors import forbidden, not_found
from megooflags_api.models import Environment, Project
from megooflags_api.services.lookup import get_environment, get_project

# (method, path) of the routes that need no credentials; each is added by the step that creates
# the route.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset()


@dataclass
class Authz:
    principal: Principal
    authorizer: Authorizer
    project: Project | None = None
    environment: Environment | None = None

    def check(
        self,
        perm: Permission,
        project_id: uuid.UUID | None = None,
        environment_id: uuid.UUID | None = None,
    ) -> None:
        """Raise 403 forbidden unless the caller holds `perm` there."""
        if not self.authorizer.can(perm, project_id, environment_id):
            raise forbidden(f"missing permission '{perm}'")


class Require:
    def __init__(
        self,
        permission: Permission | None,
        *,
        anywhere: bool = False,
        allow_enrolment: bool = False,
        session_only: bool = False,
    ) -> None:
        self.permission = permission
        self.anywhere = anywhere
        self.allow_enrolment = allow_enrolment
        self.session_only = session_only

    async def __call__(self, request: Request, session: SessionDep) -> Authz:
        principal: Principal | None = getattr(request.state, "principal", None)
        if principal is None:
            principal = await resolve_principal(
                request, session, allow_enrolment=self.allow_enrolment
            )
        else:
            check_session_state(principal, allow_enrolment=self.allow_enrolment)
        if self.session_only and principal.kind != PrincipalKind.USER:
            raise forbidden("this endpoint needs a signed-in user, not an API token")

        authorizer: Authorizer | None = getattr(request.state, "authorizer", None)
        if authorizer is None:
            authorizer = await load_authorizer(session, principal)
            request.state.authorizer = authorizer
        authz = Authz(principal, authorizer)

        project_key = request.path_params.get("project")
        if project_key is not None:
            authz.project = await get_project(session, project_key)
            if not authorizer.can(Permission.PROJECT_READ, authz.project.id):
                raise not_found("project", project_key)
            env_key = request.path_params.get("env")
            if env_key is not None:
                authz.environment = await get_environment(session, authz.project, env_key)
                if not authorizer.can_see_environment(authz.project.id, authz.environment.id):
                    raise not_found("environment", env_key)

        if self.permission is None:
            return authz
        if self.anywhere:
            if not authorizer.can_anywhere(self.permission):
                raise forbidden(f"missing permission '{self.permission}'")
            return authz
        authz.check(
            self.permission,
            None if authz.project is None else authz.project.id,
            None if authz.environment is None else authz.environment.id,
        )
        return authz


def require(permission: Permission, *, anywhere: bool = False) -> Require:
    """Needs `permission` at the path's environment, else its project, else org scope;
    `anywhere` accepts it at any scope (the handler scopes what it returns)."""
    return Require(permission, anywhere=anywhere)


def authenticated(*, allow_enrolment: bool = False, session_only: bool = False) -> Require:
    """Needs a caller, but no particular permission (path resources are still checked)."""
    return Require(None, allow_enrolment=allow_enrolment, session_only=session_only)
