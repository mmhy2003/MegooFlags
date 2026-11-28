import uuid

from megooflags_api.auth.rbac import (
    BUILTIN_ROLES,
    Authorizer,
    CappedAuthorizer,
    Grant,
    Permission,
    held,
)
from megooflags_api.models.enums import ScopeKind

P = Permission
PROJECT_P, PROJECT_Q = uuid.uuid4(), uuid.uuid4()
ENV_1, ENV_2 = uuid.uuid4(), uuid.uuid4()  # both in PROJECT_P


def org(role: str) -> Grant:
    return Grant(ScopeKind.ORG, None, None, BUILTIN_ROLES[role])


def project(role: str, project_id: uuid.UUID = PROJECT_P) -> Grant:
    return Grant(ScopeKind.PROJECT, project_id, None, BUILTIN_ROLES[role])


def env(role: str, environment_id: uuid.UUID = ENV_1) -> Grant:
    return Grant(ScopeKind.ENVIRONMENT, PROJECT_P, environment_id, BUILTIN_ROLES[role])


def test_org_admin_can_do_everything() -> None:
    authz = Authorizer([org("admin")])
    for perm in Permission:
        assert authz.can(perm, PROJECT_P, ENV_1)
        assert authz.can_anywhere(perm)
    assert authz.can(P.MEMBER_MANAGE)
    assert authz.can(P.FLAG_UPDATE, PROJECT_Q)
    assert authz.visible_project_ids() is None
    assert authz.visible_environment_ids(PROJECT_P) is None
    assert authz.can_see_environment(PROJECT_Q, ENV_2)


def test_project_editor() -> None:
    authz = Authorizer([project("editor")])
    assert authz.can(P.FLAG_UPDATE, PROJECT_P)
    assert not authz.can(P.FLAG_UPDATE, PROJECT_Q)
    assert authz.can(P.FLAG_TOGGLE, PROJECT_P, ENV_1)
    assert authz.can(P.FLAG_TOGGLE, PROJECT_P, ENV_2)
    assert not authz.can(P.PROJECT_CREATE)
    assert authz.visible_project_ids() == {PROJECT_P}
    assert authz.visible_environment_ids(PROJECT_P) is None
    assert authz.can_see_environment(PROJECT_P, ENV_2)


def test_environment_editor_toggles_only_its_environment() -> None:
    authz = Authorizer([env("editor")])
    assert authz.can(P.FLAG_TOGGLE, PROJECT_P, ENV_1)
    assert not authz.can(P.FLAG_TOGGLE, PROJECT_P, ENV_2)


def test_environment_binding_never_grants_project_level_permissions() -> None:
    authz = Authorizer([env("editor")])
    assert not authz.can(P.ENVIRONMENT_MANAGE, PROJECT_P)
    assert not authz.can(P.ENVIRONMENT_MANAGE, PROJECT_P, ENV_1)
    assert not authz.can(P.FLAG_UPDATE, PROJECT_P)
    assert not authz.can_anywhere(P.FLAG_UPDATE)


def test_environment_binding_grants_project_read_on_its_project() -> None:
    authz = Authorizer([env("editor")])
    assert authz.can(P.PROJECT_READ, PROJECT_P)
    assert not authz.can(P.PROJECT_READ, PROJECT_Q)
    assert authz.can_anywhere(P.PROJECT_READ)
    assert authz.visible_project_ids() == {PROJECT_P}


def test_environment_binding_sees_only_its_environments() -> None:
    authz = Authorizer([env("editor")])
    assert authz.can_see_environment(PROJECT_P, ENV_1)
    assert not authz.can_see_environment(PROJECT_P, ENV_2)
    assert authz.visible_environment_ids(PROJECT_P) == {ENV_1}
    assert authz.visible_environment_ids(PROJECT_Q) == set()


def test_visible_project_ids_is_none_for_org_level_read() -> None:
    assert Authorizer([org("viewer")]).visible_project_ids() is None
    assert Authorizer([]).visible_project_ids() == set()


def test_custom_role_at_org_toggles_everywhere_and_nothing_else() -> None:
    authz = Authorizer([Grant(ScopeKind.ORG, None, None, frozenset({P.FLAG_TOGGLE}))])
    assert authz.can(P.FLAG_TOGGLE, PROJECT_P, ENV_1)
    assert authz.can(P.FLAG_TOGGLE, PROJECT_Q, ENV_2)
    for perm in set(Permission) - {P.FLAG_TOGGLE}:
        assert not authz.can(perm, PROJECT_P, ENV_1)
        assert not authz.can_anywhere(perm)


def test_audit_read_at_project_scope() -> None:
    authz = Authorizer([Grant(ScopeKind.PROJECT, PROJECT_P, None, frozenset({P.AUDIT_READ}))])
    assert authz.can(P.AUDIT_READ, PROJECT_P)
    assert not authz.can(P.AUDIT_READ)
    assert not authz.can(P.AUDIT_READ, PROJECT_Q)
    assert authz.can_anywhere(P.AUDIT_READ)
    assert authz.projects_with(P.AUDIT_READ) == {PROJECT_P}
    assert Authorizer([org("viewer")]).projects_with(P.AUDIT_READ) is None


def test_grants_union() -> None:
    authz = Authorizer([project("viewer"), project("editor", PROJECT_Q), env("editor", ENV_2)])
    assert not authz.can(P.FLAG_UPDATE, PROJECT_P)
    assert authz.can(P.FLAG_UPDATE, PROJECT_Q)
    assert authz.can(P.FLAG_TOGGLE, PROJECT_P, ENV_2)
    assert not authz.can(P.FLAG_TOGGLE, PROJECT_P, ENV_1)
    assert authz.can(P.AUDIT_READ, PROJECT_P)
    assert not authz.can(P.AUDIT_READ, PROJECT_Q)
    assert authz.visible_project_ids() == {PROJECT_P, PROJECT_Q}
    assert authz.projects_with(P.FLAG_UPDATE) == {PROJECT_Q}


def test_capped_authorizer_limits_an_admin_to_the_token() -> None:
    token = Grant(ScopeKind.PROJECT, PROJECT_P, None, frozenset({P.PROJECT_READ}))
    authz = CappedAuthorizer(Authorizer([org("admin")]), token)
    assert authz.can(P.PROJECT_READ, PROJECT_P)
    assert not authz.can(P.PROJECT_READ, PROJECT_Q)
    assert not authz.can(P.FLAG_UPDATE, PROJECT_P)
    assert not authz.can(P.MEMBER_MANAGE)
    assert authz.visible_project_ids() == {PROJECT_P}
    assert authz.can_see_environment(PROJECT_P, ENV_1)
    assert not authz.can_see_environment(PROJECT_Q, ENV_2)


def test_capped_authorizer_never_exceeds_the_owner() -> None:
    token = Grant(ScopeKind.ORG, None, None, frozenset({P.FLAG_UPDATE}))
    authz = CappedAuthorizer(Authorizer([org("viewer")]), token)
    assert not authz.can(P.FLAG_UPDATE, PROJECT_P)
    assert not authz.can_anywhere(P.FLAG_UPDATE)
    assert authz.projects_with(P.FLAG_UPDATE) == set()


def test_held_lists_permissions_of_the_levels_in_catalogue_order() -> None:
    assert held(Authorizer([org("viewer")]), ScopeKind) == [
        P.AUDIT_READ,
        P.PROJECT_READ,
        P.EVALUATE,
        P.SDK_KEY_READ,
    ]
