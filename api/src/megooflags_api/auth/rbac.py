"""The permission catalogue (permissions, the scope level each can be granted at, the built-in
roles) and the `Authorizer` that answers permission checks from a principal's role bindings."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.principal import Principal, PrincipalKind
from megooflags_api.models import Role, RoleBinding, TeamMember
from megooflags_api.models.enums import ScopeKind, SubjectKind


class Permission(StrEnum):
    ORG_MANAGE = "org.manage"
    MEMBER_MANAGE = "member.manage"
    ROLE_MANAGE = "role.manage"
    TOKEN_MANAGE = "token.manage"
    PROJECT_CREATE = "project.create"
    AUDIT_READ = "audit.read"
    PROJECT_READ = "project.read"
    PROJECT_MANAGE = "project.manage"
    ENVIRONMENT_MANAGE = "environment.manage"
    FLAG_CREATE = "flag.create"
    FLAG_UPDATE = "flag.update"
    FLAG_ARCHIVE = "flag.archive"
    SEGMENT_WRITE = "segment.write"
    FLAG_CONFIG_WRITE = "flag.config.write"
    FLAG_TOGGLE = "flag.toggle"
    EVALUATE = "evaluate"
    SDK_KEY_READ = "sdk_key.read"
    SDK_KEY_MANAGE = "sdk_key.manage"


P = Permission

# The most specific scope each permission can be granted at. A binding grants a permission only
# when the binding's scope is that level or broader.
PERMISSION_LEVEL: dict[Permission, ScopeKind] = {
    **dict.fromkeys(
        [P.ORG_MANAGE, P.MEMBER_MANAGE, P.ROLE_MANAGE, P.TOKEN_MANAGE, P.PROJECT_CREATE],
        ScopeKind.ORG,
    ),
    **dict.fromkeys(
        [
            P.AUDIT_READ,
            P.PROJECT_READ,
            P.PROJECT_MANAGE,
            P.ENVIRONMENT_MANAGE,
            P.FLAG_CREATE,
            P.FLAG_UPDATE,
            P.FLAG_ARCHIVE,
            P.SEGMENT_WRITE,
        ],
        ScopeKind.PROJECT,
    ),
    **dict.fromkeys(
        [P.FLAG_CONFIG_WRITE, P.FLAG_TOGGLE, P.EVALUATE, P.SDK_KEY_READ, P.SDK_KEY_MANAGE],
        ScopeKind.ENVIRONMENT,
    ),
}

# Seeded by migration 0004 (as literal lists); read-only.
BUILTIN_ROLES: dict[str, frozenset[Permission]] = {
    "admin": frozenset(Permission),
    "editor": frozenset(Permission)
    - {P.ORG_MANAGE, P.MEMBER_MANAGE, P.ROLE_MANAGE, P.TOKEN_MANAGE, P.AUDIT_READ},
    "viewer": frozenset({P.PROJECT_READ, P.EVALUATE, P.SDK_KEY_READ, P.AUDIT_READ}),
}

RANK: dict[ScopeKind, int] = {ScopeKind.ORG: 0, ScopeKind.PROJECT: 1, ScopeKind.ENVIRONMENT: 2}


@dataclass(frozen=True)
class Grant:
    """A set of permissions held at one scope (a role binding, or a personal token's scope)."""

    scope: ScopeKind
    project_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    permissions: frozenset[Permission]

    def allows(self, perm: Permission) -> bool:
        """Holds `perm` at a scope it can be granted at (its level or broader)."""
        return perm in self.permissions and RANK[self.scope] <= RANK[PERMISSION_LEVEL[perm]]

    def covers(self, project_id: uuid.UUID | None, environment_id: uuid.UUID | None) -> bool:
        match self.scope:
            case ScopeKind.ORG:
                return True
            case ScopeKind.PROJECT:
                return project_id is not None and self.project_id == project_id
            case ScopeKind.ENVIRONMENT:
                return environment_id is not None and self.environment_id == environment_id


class Authorizer:
    """Answers permission checks from a principal's grants. Allow-only: grants union."""

    def __init__(self, grants: Iterable[Grant]) -> None:
        self.grants = tuple(grants)

    def _env_grants(self) -> list[Grant]:
        return [g for g in self.grants if g.scope == ScopeKind.ENVIRONMENT]

    def _strict_read(self, project_id: uuid.UUID) -> bool:
        """project.read from an org or project grant (not implied by an environment grant)."""
        return any(g.allows(P.PROJECT_READ) and g.covers(project_id, None) for g in self.grants)

    def can(
        self,
        perm: Permission,
        project_id: uuid.UUID | None = None,
        environment_id: uuid.UUID | None = None,
    ) -> bool:
        if any(g.allows(perm) and g.covers(project_id, environment_id) for g in self.grants):
            return True
        # An environment binding also grants project.read on its project.
        return perm == P.PROJECT_READ and any(
            g.project_id == project_id for g in self._env_grants()
        )

    def can_anywhere(self, perm: Permission) -> bool:
        if any(g.allows(perm) for g in self.grants):
            return True
        return perm == P.PROJECT_READ and bool(self._env_grants())

    def can_see_environment(self, project_id: uuid.UUID, environment_id: uuid.UUID) -> bool:
        return self._strict_read(project_id) or any(
            g.environment_id == environment_id for g in self._env_grants()
        )

    def projects_with(self, perm: Permission) -> set[uuid.UUID] | None:
        """The projects where `perm` is held; None means org-wide."""
        if any(g.scope == ScopeKind.ORG and g.allows(perm) for g in self.grants):
            return None
        ids = {g.project_id for g in self.grants if g.allows(perm) and g.project_id is not None}
        if perm == P.PROJECT_READ:
            ids |= {g.project_id for g in self._env_grants() if g.project_id is not None}
        return ids

    def visible_project_ids(self) -> set[uuid.UUID] | None:
        """The projects the principal can see; None means all."""
        return self.projects_with(P.PROJECT_READ)

    def visible_environment_ids(self, project_id: uuid.UUID) -> set[uuid.UUID] | None:
        """The environments of `project_id` the principal can see; None means all."""
        if self._strict_read(project_id):
            return None
        return {
            g.environment_id
            for g in self._env_grants()
            if g.project_id == project_id and g.environment_id is not None
        }


def _intersect(a: set[uuid.UUID] | None, b: set[uuid.UUID] | None) -> set[uuid.UUID] | None:
    if a is None:
        return b
    if b is None:
        return a
    return a & b


class CappedAuthorizer(Authorizer):
    """A personal token: its own permissions and scope, never more than its owner's current
    rights."""

    def __init__(self, owner: Authorizer, token: Grant) -> None:
        super().__init__([token])
        self.owner = owner

    def can(
        self,
        perm: Permission,
        project_id: uuid.UUID | None = None,
        environment_id: uuid.UUID | None = None,
    ) -> bool:
        return super().can(perm, project_id, environment_id) and self.owner.can(
            perm, project_id, environment_id
        )

    def can_anywhere(self, perm: Permission) -> bool:
        return super().can_anywhere(perm) and self.owner.can_anywhere(perm)

    def can_see_environment(self, project_id: uuid.UUID, environment_id: uuid.UUID) -> bool:
        return super().can_see_environment(
            project_id, environment_id
        ) and self.owner.can_see_environment(project_id, environment_id)

    def projects_with(self, perm: Permission) -> set[uuid.UUID] | None:
        return _intersect(super().projects_with(perm), self.owner.projects_with(perm))

    def visible_environment_ids(self, project_id: uuid.UUID) -> set[uuid.UUID] | None:
        return _intersect(
            super().visible_environment_ids(project_id),
            self.owner.visible_environment_ids(project_id),
        )


_VALUES = frozenset(p.value for p in Permission)


def _permissions(values: Iterable[str]) -> frozenset[Permission]:
    """Catalogue permissions only; a value dropped from the catalogue grants nothing."""
    return frozenset(Permission(v) for v in values if v in _VALUES)


def _scope_of(project_id: uuid.UUID | None, environment_id: uuid.UUID | None) -> ScopeKind:
    if environment_id is not None:
        return ScopeKind.ENVIRONMENT
    return ScopeKind.PROJECT if project_id is not None else ScopeKind.ORG


async def load_authorizer(session: AsyncSession, principal: Principal) -> Authorizer:
    """The principal's authorizer, from one SELECT of its role bindings.

    A user gets its own and its teams' bindings; a service token its own; a personal token its
    owner's, capped by the token's permissions and scope.
    """
    subject: ColumnElement[bool]
    if principal.kind == PrincipalKind.SERVICE_TOKEN:
        subject = (RoleBinding.subject_kind == SubjectKind.SERVICE_TOKEN) & (
            RoleBinding.subject_id == principal.id
        )
    else:
        teams = select(TeamMember.team_id).where(TeamMember.user_id == principal.user_id)
        subject = or_(
            (RoleBinding.subject_kind == SubjectKind.USER)
            & (RoleBinding.subject_id == principal.user_id),
            (RoleBinding.subject_kind == SubjectKind.TEAM) & RoleBinding.subject_id.in_(teams),
        )
    rows = await session.execute(
        select(
            RoleBinding.scope, RoleBinding.project_id, RoleBinding.environment_id, Role.permissions
        )
        .join(Role, Role.id == RoleBinding.role_id)
        .where(subject)
    )
    authorizer = Authorizer(
        Grant(scope, project_id, environment_id, _permissions(permissions))
        for scope, project_id, environment_id, permissions in rows
    )
    if principal.kind != PrincipalKind.PERSONAL_TOKEN:
        return authorizer
    token = Grant(
        _scope_of(principal.token_project_id, principal.token_environment_id),
        principal.token_project_id,
        principal.token_environment_id,
        _permissions(principal.token_permissions or ()),
    )
    return CappedAuthorizer(authorizer, token)
