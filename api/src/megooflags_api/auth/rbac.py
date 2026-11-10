"""The permission catalogue: permissions, the scope level each can be granted at, and the
built-in roles."""

from enum import StrEnum

from megooflags_api.models.enums import ScopeKind


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
