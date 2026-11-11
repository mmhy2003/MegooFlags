"""Who an SSO sign-in is (linking and just-in-time provisioning), and group sync.

Group sync adds and removes only the team memberships and role bindings whose source is
`sso:<provider key>`; manual ones are never touched, and a manual one that a mapping would also
grant is left as it is. It never removes the last active org admin: those removals are skipped,
with a warning.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.principal import Principal
from megooflags_api.auth.sso import SsoAssertion, SsoDenied
from megooflags_api.errors import ApiError
from megooflags_api.models import (
    Environment,
    Project,
    Role,
    RoleBinding,
    SsoGroupMapping,
    SsoProvider,
    Team,
    TeamMember,
    User,
    UserIdentity,
)
from megooflags_api.models.enums import ScopeKind, SubjectKind, UserStatus
from megooflags_api.services.audit import RequestMeta, record
from megooflags_api.services.bindings import active_org_admin_ids, guard_last_admin
from megooflags_api.services.users import create_user, get_user_by_email

logger = logging.getLogger(__name__)

MAX_EMAIL = 320
MAX_NAME = 200

# (role_id, scope, project_id, environment_id)
BindingTarget = tuple[uuid.UUID, ScopeKind, uuid.UUID | None, uuid.UUID | None]


def source_of(provider: SsoProvider) -> str:
    return f"sso:{provider.key}"


def _check_active(user: User) -> None:
    if user.status != UserStatus.ACTIVE:
        raise SsoDenied("sso_user_disabled", f"{user.email} is disabled")


async def resolve_user(
    session: AsyncSession, provider: SsoProvider, assertion: SsoAssertion
) -> User:
    """The user the assertion signs in, linking or creating one when the provider allows it.

    Raises SsoDenied: `sso_user_disabled`, `sso_email_conflict` (a local account has the email and
    linking is off or the email is not verified), `sso_not_provisioned` (JIT is off), or
    `sso_failed` (no usable, verified email to provision with: an unverified one would claim the
    address for good). Flushes; the caller commits.
    """
    user = await session.scalar(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.provider_id == provider.id, UserIdentity.subject == assertion.subject)
    )
    if user is not None:
        _check_active(user)
        return user

    email = assertion.email
    if email is not None:
        existing = await get_user_by_email(session, email)
        if existing is not None:
            if not (provider.link_by_verified_email and assertion.email_verified):
                raise SsoDenied(
                    "sso_email_conflict", f"a local account already uses {email}; not linked"
                )
            _check_active(existing)
            await _link(session, provider, assertion, existing)
            return existing

    if not provider.jit_enabled:
        raise SsoDenied("sso_not_provisioned", f"no account for {email or assertion.subject}")
    if email is None or len(email) > MAX_EMAIL:
        raise SsoDenied("sso_failed", "the identity provider sent no usable email")
    if not assertion.email_verified:
        raise SsoDenied("sso_failed", f"the identity provider has not verified {email}")
    name = (assertion.name or email)[:MAX_NAME]
    user = await create_user(session, email, name, None)
    await _link(session, provider, assertion, user)
    return user


async def _link(
    session: AsyncSession, provider: SsoProvider, assertion: SsoAssertion, user: User
) -> None:
    session.add(UserIdentity(provider_id=provider.id, subject=assertion.subject, user_id=user.id))
    await session.flush()


async def _desired(
    session: AsyncSession, provider: SsoProvider, groups: list[str]
) -> tuple[set[uuid.UUID], set[BindingTarget]]:
    teams: set[uuid.UUID] = set()
    targets: set[BindingTarget] = set()
    if groups:
        mappings = await session.scalars(
            select(SsoGroupMapping).where(
                SsoGroupMapping.provider_id == provider.id, SsoGroupMapping.idp_group.in_(groups)
            )
        )
        for m in mappings:
            if m.team_id is not None:
                teams.add(m.team_id)
            else:
                assert m.role_id is not None and m.scope is not None  # the one_target check
                targets.add((m.role_id, m.scope, m.project_id, m.environment_id))
    if provider.default_role_id is not None:
        targets.add((provider.default_role_id, ScopeKind.ORG, None, None))
    return teams, targets


async def _describe(
    session: AsyncSession, teams: set[uuid.UUID], targets: set[BindingTarget]
) -> dict[str, Any]:
    """Teams and bindings by key, for the audit entry."""
    team_keys = await _keys(session, Team.key, Team.id, teams)
    role_keys = await _keys(session, Role.key, Role.id, {t[0] for t in targets})
    project_keys = await _keys(session, Project.key, Project.id, {t[2] for t in targets if t[2]})
    env_keys = await _keys(
        session, Environment.key, Environment.id, {t[3] for t in targets if t[3]}
    )
    bindings = [
        {
            "role": role_keys.get(role_id),
            "scope": scope.value,
            "project": None if project_id is None else project_keys.get(project_id),
            "env": None if env_id is None else env_keys.get(env_id),
        }
        for role_id, scope, project_id, env_id in targets
    ]
    bindings.sort(key=lambda b: tuple(b[k] or "" for k in ("role", "scope", "project", "env")))
    return {"teams": sorted(team_keys[t] for t in teams if t in team_keys), "bindings": bindings}


async def _keys(
    session: AsyncSession, key_column: Any, id_column: Any, ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = await session.execute(select(id_column, key_column).where(id_column.in_(ids)))
    return {row_id: key for row_id, key in rows}


async def _remove(
    session: AsyncSession,
    user: User,
    source: str,
    teams: set[uuid.UUID],
    binding_ids: list[uuid.UUID],
) -> bool:
    """Remove the memberships and bindings, unless that leaves no active org admin; then keep
    them, log a warning and return False."""
    was_admin = user.id in await active_org_admin_ids(session)
    try:
        async with session.begin_nested():
            if teams:
                await session.execute(
                    delete(TeamMember).where(
                        TeamMember.user_id == user.id,
                        TeamMember.source == source,
                        TeamMember.team_id.in_(teams),
                    )
                )
            if binding_ids:
                await session.execute(delete(RoleBinding).where(RoleBinding.id.in_(binding_ids)))
            if was_admin:
                await guard_last_admin(session)
    except ApiError as exc:
        if exc.code != "last_admin":
            raise
        logger.warning(
            "group sync kept the %s access of %s: it would leave no active org admin",
            source,
            user.email,
        )
        return False
    return True


async def sync_groups(
    session: AsyncSession,
    provider: SsoProvider,
    user: User,
    groups: list[str],
    principal: Principal,
    meta: RequestMeta,
) -> None:
    """Bring the user's `sso:<key>` memberships and bindings in line with their IdP groups and the
    provider's default role, and audit `sso.sync` when anything changed. The caller commits."""
    source = source_of(provider)
    want_teams, want_targets = await _desired(session, provider, groups)

    members = list(await session.scalars(select(TeamMember).where(TeamMember.user_id == user.id)))
    have_teams = {m.team_id for m in members if m.source == source}
    other_teams = {m.team_id for m in members if m.source != source}
    user_bindings = list(
        await session.scalars(
            select(RoleBinding).where(
                RoleBinding.subject_kind == SubjectKind.USER, RoleBinding.subject_id == user.id
            )
        )
    )

    def target(b: RoleBinding) -> BindingTarget:
        return (b.role_id, b.scope, b.project_id, b.environment_id)

    have_targets = {target(b) for b in user_bindings if b.source == source}
    other_targets = {target(b) for b in user_bindings if b.source != source}

    new_teams = want_teams - have_teams - other_teams
    new_targets = want_targets - have_targets - other_targets
    gone_teams = have_teams - want_teams
    gone_bindings = [
        b.id for b in user_bindings if b.source == source and target(b) not in want_targets
    ]
    if not (new_teams or new_targets or gone_teams or gone_bindings):
        return

    before = await _describe(session, have_teams, have_targets)
    if gone_teams or gone_bindings:
        if not await _remove(session, user, source, gone_teams, gone_bindings):
            gone_teams, gone_bindings = set(), []
            if not (new_teams or new_targets):
                return
    session.add_all(TeamMember(team_id=t, user_id=user.id, source=source) for t in new_teams)
    session.add_all(
        RoleBinding(
            subject_kind=SubjectKind.USER,
            subject_id=user.id,
            role_id=role_id,
            scope=scope,
            project_id=project_id,
            environment_id=env_id,
            source=source,
        )
        for role_id, scope, project_id, env_id in new_targets
    )
    await session.flush()
    after = await _describe(
        session, (have_teams - gone_teams) | new_teams, (have_targets & want_targets) | new_targets
    )
    await record(
        session, principal, meta, "sso.sync", "user", user.email, before=before, after=after
    )
