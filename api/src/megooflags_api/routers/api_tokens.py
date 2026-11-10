"""Service API tokens (`/tokens`) and their role bindings. Rotation is `/tokens/{id}/rotate`."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from megooflags_api.auth.rbac import Permission
from megooflags_api.auth.require import Authz, require
from megooflags_api.db import SessionDep
from megooflags_api.errors import ERROR_RESPONSES
from megooflags_api.models.enums import SubjectKind
from megooflags_api.schemas.common import Page
from megooflags_api.schemas.identity import BindingIn
from megooflags_api.schemas.tokens import ServiceTokenCreate, TokenCreated, TokenOut
from megooflags_api.services import api_tokens, bindings
from megooflags_api.services.audit import AuditDep, snapshot

router = APIRouter(prefix="/tokens", tags=["tokens"], responses=ERROR_RESPONSES)

ManageTokens = Annotated[Authz, Depends(require(Permission.TOKEN_MANAGE))]


@router.get("")
async def list_tokens(
    session: SessionDep,
    authz: ManageTokens,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> Page[TokenOut]:
    items, next_cursor = await api_tokens.list_service_tokens(session, cursor, limit)
    return Page[TokenOut](items=items, next_cursor=next_cursor)


@router.post("", status_code=201)
async def create_token(
    session: SessionDep, authz: ManageTokens, audit: AuditDep, body: ServiceTokenCreate
) -> TokenCreated:
    """Create a service token holding `bindings`. The secret is returned only here."""
    row, secret = await api_tokens.create_service_token(
        session, body.name, created_by=authz.principal.id, expires_at=body.expires_at
    )
    for binding in body.bindings:
        await bindings.create_binding(
            session,
            BindingIn(
                subject_kind=SubjectKind.SERVICE_TOKEN,
                subject_id=row.id,
                role=binding.role,
                scope=binding.scope,
                project=binding.project,
                env=binding.env,
            ),
        )
    out = await api_tokens.token_out(session, row)
    await audit.record("token.create", "token", str(row.id), after=snapshot(out))
    await session.commit()
    return TokenCreated(token=out, secret=secret)


@router.get("/{id}")
async def read_token(session: SessionDep, authz: ManageTokens, id: uuid.UUID) -> TokenOut:
    return await api_tokens.token_out(session, await api_tokens.get_service_token(session, id))


@router.delete("/{id}", status_code=204)
async def revoke_token(
    session: SessionDep, authz: ManageTokens, audit: AuditDep, id: uuid.UUID
) -> Response:
    """Revoke the token at once. Its role bindings stay, so the record still shows them."""
    row = await api_tokens.get_service_token(session, id)
    before = await api_tokens.token_out(session, row)
    await api_tokens.revoke(session, row)
    after = await api_tokens.token_out(session, row)
    await audit.record(
        "token.revoke", "token", str(id), before=snapshot(before), after=snapshot(after)
    )
    await session.commit()
    return Response(status_code=204)
