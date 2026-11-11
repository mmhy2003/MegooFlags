import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, pg_enum, uuid_pk
from megooflags_api.models.enums import ScopeKind, SsoProtocol


class SsoProvider(TimestampMixin, Base):
    """An OIDC or SAML identity provider people can sign in with."""

    __tablename__ = "sso_provider"
    __table_args__ = (UniqueConstraint("key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    protocol: Mapped[SsoProtocol] = mapped_column(pg_enum(SsoProtocol, "sso_protocol"))
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    # The OidcConfig or SamlConfig of the protocol (SAML also keeps the fetched metadata XML).
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # SecretBox-encrypted JSON: {"client_secret"} (OIDC) or {"sp_private_key", "sp_certificate"}.
    secret_encrypted: Mapped[str | None] = mapped_column(Text)
    jit_enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    link_by_verified_email: Mapped[bool] = mapped_column(default=False, server_default="false")
    # Bound at org scope to every user who signs in through this provider.
    default_role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("role.id", ondelete="RESTRICT")
    )


class SsoGroupMapping(Base):
    """Maps an IdP group to a team, or to a role at a scope, for group sync."""

    __tablename__ = "sso_group_mapping"
    __table_args__ = (
        CheckConstraint(
            "(team_id IS NOT NULL AND role_id IS NULL AND scope IS NULL"
            " AND project_id IS NULL AND environment_id IS NULL)"
            " OR (team_id IS NULL AND role_id IS NOT NULL AND ("
            "(scope = 'org' AND project_id IS NULL AND environment_id IS NULL)"
            " OR (scope = 'project' AND project_id IS NOT NULL AND environment_id IS NULL)"
            " OR (scope = 'environment' AND project_id IS NOT NULL"
            " AND environment_id IS NOT NULL)))",
            name="one_target",
        ),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sso_provider.id", ondelete="CASCADE"), index=True
    )
    idp_group: Mapped[str] = mapped_column(String(500))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("team.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("role.id", ondelete="RESTRICT"))
    scope: Mapped[ScopeKind | None] = mapped_column(pg_enum(ScopeKind, "scope_kind"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE")
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE")
    )


class UserIdentity(Base):
    """Links a user to their subject at an SSO provider."""

    __tablename__ = "user_identity"
    __table_args__ = (UniqueConstraint("provider_id", "subject"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sso_provider.id", ondelete="CASCADE")
    )
    subject: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
