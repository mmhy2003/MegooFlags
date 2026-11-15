"""Create, read, update and delete a project's integrations, and rotate webhook secrets.

The URL and the signing secret are stored encrypted; reads only ever see `url_hint`.
"""

import secrets
import uuid
from collections import defaultdict
from collections.abc import Sequence
from ipaddress import ip_address
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.auth.rbac import Authorizer
from megooflags_api.crypto import SecretBox
from megooflags_api.errors import not_found, validation_error
from megooflags_api.models import Environment, Integration, IntegrationEnvironment, Project
from megooflags_api.models.enums import IntegrationKind
from megooflags_api.schemas.integrations import IntegrationCreate, IntegrationOut, IntegrationUpdate
from megooflags_api.services.integrations.net import UnsafeUrl, is_allowed_ip, resolve, validate_url

SECRET_PREFIX = "mf_whsec_"
DEFAULT_PORTS = {"https": 443, "http": 80}


def url_hint(url: str) -> str:
    """`scheme://host/…` and the last four characters of the path; no query, port or user."""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.hostname}"
    if parts.path in ("", "/"):
        return base
    return f"{base}/…{parts.path[-4:]}"


def new_secret() -> str:
    return SECRET_PREFIX + secrets.token_hex(16)


async def check_url(url: str, kind: IntegrationKind, *, allow_private: bool) -> None:
    """422 at `url` unless the URL is safe and every address of its host is allowed.

    The worker checks the addresses again when it connects; this only refuses bad URLs early.
    Messages name the host at most, never the URL, which may carry a credential.
    """
    try:
        validate_url(url, kind, allow_private=allow_private)
        parts = urlsplit(url)
        host = parts.hostname
        assert host is not None  # validate_url requires one
        try:
            answers = await resolve(host, parts.port or DEFAULT_PORTS[parts.scheme])
        except OSError as exc:
            raise UnsafeUrl(f"unsafe address: cannot resolve {host}") from exc
        if not answers:
            raise UnsafeUrl(f"unsafe address: cannot resolve {host}")
        for answer in answers:
            try:
                address = ip_address(answer)
            except ValueError as exc:
                raise UnsafeUrl(f"unsafe address: {host} resolved to an invalid address") from exc
            if not is_allowed_ip(address, allow_private=allow_private):
                raise UnsafeUrl(f"unsafe address: {host} resolves to {address}")
    except UnsafeUrl as exc:
        raise validation_error(str(exc), "url") from exc


async def _environments(
    session: AsyncSession, project: Project, keys: Sequence[str], authorizer: Authorizer
) -> list[Environment]:
    """The project's environments with these keys; 422 when one is unknown or hidden."""
    found = await session.scalars(
        select(Environment).where(Environment.project_id == project.id, Environment.key.in_(keys))
    )
    rows = {e.key: e for e in found}
    for key in keys:
        row = rows.get(key)
        if row is None or not authorizer.can_see_environment(project.id, row.id):
            raise validation_error(f"environment '{key}' not found", "environments")
    return list(rows.values())


def _check_scope(all_environments: bool, environments: Sequence[object]) -> None:
    if not all_environments and not environments:
        raise validation_error(
            "choose at least one environment, or all environments", "environments"
        )


async def _environment_keys(
    session: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """The chosen environment keys of each integration, in environment order."""
    keys: dict[uuid.UUID, list[str]] = defaultdict(list)
    if not ids:
        return keys
    rows = await session.execute(
        select(IntegrationEnvironment.integration_id, Environment.key)
        .join(Environment, Environment.id == IntegrationEnvironment.environment_id)
        .where(IntegrationEnvironment.integration_id.in_(ids))
        .order_by(Environment.sort_order, Environment.key)
    )
    for integration_id, key in rows:
        keys[integration_id].append(key)
    return keys


def _out(row: Integration, environments: list[str]) -> IntegrationOut:
    return IntegrationOut.model_validate(
        {
            "id": row.id,
            "kind": row.kind,
            "name": row.name,
            "url_hint": row.url_hint,
            "event_groups": row.event_groups,
            "all_environments": row.all_environments,
            "environments": environments,
            "enabled": row.enabled,
            "last_delivery_at": row.last_delivery_at,
            "last_delivery_ok": row.last_delivery_ok,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


async def integration_out(session: AsyncSession, row: Integration) -> IntegrationOut:
    return _out(row, (await _environment_keys(session, [row.id]))[row.id])


async def list_integrations(session: AsyncSession, project: Project) -> list[IntegrationOut]:
    rows = (
        await session.scalars(
            select(Integration)
            .where(Integration.project_id == project.id)
            .order_by(Integration.name, Integration.id)
        )
    ).all()
    keys = await _environment_keys(session, [r.id for r in rows])
    return [_out(r, keys[r.id]) for r in rows]


async def get_integration(
    session: AsyncSession, project: Project, id: uuid.UUID, *, for_update: bool = False
) -> Integration:
    query = select(Integration).where(Integration.id == id, Integration.project_id == project.id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = await session.scalar(query)
    if row is None:
        raise not_found("integration", str(id))
    return row


async def _set_environments(
    session: AsyncSession, row: Integration, environments: Sequence[Environment]
) -> None:
    await session.execute(
        delete(IntegrationEnvironment).where(IntegrationEnvironment.integration_id == row.id)
    )
    session.add_all(
        IntegrationEnvironment(integration_id=row.id, environment_id=e.id) for e in environments
    )


async def create_integration(
    session: AsyncSession,
    project: Project,
    body: IntegrationCreate,
    box: SecretBox,
    authorizer: Authorizer,
    *,
    allow_private: bool,
) -> tuple[Integration, str | None]:
    """The new row and, for a webhook, its signing secret."""
    await check_url(body.url, body.kind, allow_private=allow_private)
    environments = await _environments(session, project, body.environments, authorizer)
    _check_scope(body.all_environments, environments)
    secret = new_secret() if body.kind is IntegrationKind.WEBHOOK else None
    row = Integration(
        project_id=project.id,
        kind=body.kind,
        name=body.name,
        url_encrypted=box.encrypt(body.url),
        url_hint=url_hint(body.url),
        secret_encrypted=None if secret is None else box.encrypt(secret),
        event_groups=list(dict.fromkeys(g.value for g in body.event_groups)),
        all_environments=body.all_environments,
        enabled=body.enabled,
    )
    session.add(row)
    await session.flush()
    if not body.all_environments:
        await _set_environments(session, row, environments)
        await session.flush()
    return row, secret


async def update_integration(
    session: AsyncSession,
    project: Project,
    row: Integration,
    body: IntegrationUpdate,
    box: SecretBox,
    authorizer: Authorizer,
    *,
    allow_private: bool,
) -> None:
    if body.url is not None:
        await check_url(body.url, row.kind, allow_private=allow_private)
    all_environments = (
        row.all_environments if body.all_environments is None else body.all_environments
    )
    if body.environments is not None:
        environments = await _environments(session, project, body.environments, authorizer)
    else:
        current = await session.scalars(
            select(Environment)
            .join(IntegrationEnvironment, IntegrationEnvironment.environment_id == Environment.id)
            .where(IntegrationEnvironment.integration_id == row.id)
        )
        environments = list(current)
    _check_scope(all_environments, environments)

    if body.name is not None:
        row.name = body.name
    if body.url is not None:
        row.url_encrypted = box.encrypt(body.url)
        row.url_hint = url_hint(body.url)
    if body.event_groups is not None:
        row.event_groups = list(dict.fromkeys(g.value for g in body.event_groups))
    if body.enabled is not None:
        row.enabled = body.enabled
    row.all_environments = all_environments
    await _set_environments(session, row, [] if all_environments else environments)
    row.updated_at = func.now()  # also when only the environments changed
    await session.flush()
    await session.refresh(row)  # load the updated_at the database set


async def rotate_secret(session: AsyncSession, row: Integration, box: SecretBox) -> str:
    if row.kind is not IntegrationKind.WEBHOOK:
        raise validation_error("only webhooks have a signing secret", "kind")
    secret = new_secret()
    row.secret_encrypted = box.encrypt(secret)
    await session.flush()
    await session.refresh(row)
    return secret


async def delete_integration(session: AsyncSession, row: Integration) -> None:
    await session.delete(row)
    await session.flush()
