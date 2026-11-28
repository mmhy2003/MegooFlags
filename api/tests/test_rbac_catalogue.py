from megooflags_api.auth.rbac import BUILTIN_ROLES, PERMISSION_LEVEL, RANK, Permission
from megooflags_api.models.enums import ScopeKind

P = Permission


def test_every_permission_has_a_level() -> None:
    assert set(PERMISSION_LEVEL) == set(Permission)


def test_levels() -> None:
    assert PERMISSION_LEVEL[P.AUDIT_READ] is ScopeKind.PROJECT
    assert PERMISSION_LEVEL[P.FLAG_TOGGLE] is ScopeKind.ENVIRONMENT
    assert PERMISSION_LEVEL[P.CHANGE_APPROVE] is ScopeKind.ENVIRONMENT
    assert PERMISSION_LEVEL[P.ORG_MANAGE] is ScopeKind.ORG


def test_rank_orders_scopes() -> None:
    assert RANK == {ScopeKind.ORG: 0, ScopeKind.PROJECT: 1, ScopeKind.ENVIRONMENT: 2}


def test_builtin_roles_match_the_catalogue() -> None:
    assert set(BUILTIN_ROLES) == {"admin", "editor", "viewer"}
    assert BUILTIN_ROLES["admin"] == frozenset(Permission)
    assert BUILTIN_ROLES["editor"] == frozenset(Permission) - {
        P.ORG_MANAGE,
        P.MEMBER_MANAGE,
        P.ROLE_MANAGE,
        P.TOKEN_MANAGE,
        P.AUDIT_READ,
    }
    assert BUILTIN_ROLES["viewer"] == {P.PROJECT_READ, P.EVALUATE, P.SDK_KEY_READ, P.AUDIT_READ}


def test_permission_values() -> None:
    assert [p.value for p in Permission] == [
        "org.manage",
        "member.manage",
        "role.manage",
        "token.manage",
        "project.create",
        "audit.read",
        "project.read",
        "project.manage",
        "environment.manage",
        "flag.create",
        "flag.update",
        "flag.archive",
        "segment.write",
        "flag.config.write",
        "flag.toggle",
        "change.approve",
        "evaluate",
        "sdk_key.read",
        "sdk_key.manage",
    ]
