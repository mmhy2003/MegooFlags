import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base
from megooflags_api.models.base import TimestampMixin, pg_enum, uuid_pk
from megooflags_api.models.enums import (
    ApiTokenKind,
    InvitationPurpose,
    ScopeKind,
    SessionState,
    SubjectKind,
    UserStatus,
    UserTheme,
)


class User(Base):
    __tablename__ = "user"
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"),
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    theme: Mapped[UserTheme] = mapped_column(
        pg_enum(UserTheme, "user_theme"),
        default=UserTheme.SYSTEM,
        server_default=UserTheme.SYSTEM.value,
    )
    sidebar_collapsed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    __table_args__ = (Index("uq_user_email_lower", func.lower(email), unique=True),)


class Team(Base):
    __tablename__ = "team"
    __table_args__ = (UniqueConstraint("key"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamMember(Base):
    __tablename__ = "team_member"

    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # "manual", or "sso:<provider key>" for memberships managed by group sync.
    source: Mapped[str] = mapped_column(Text, default="manual", server_default="manual")


class Role(TimestampMixin, Base):
    __tablename__ = "role"
    __table_args__ = (UniqueConstraint("key"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    builtin: Mapped[bool] = mapped_column(default=False, server_default="false")
    permissions: Mapped[list[str]] = mapped_column(ARRAY(Text))


class RoleBinding(Base):
    """Gives a user, team or service token a role at org, project or environment scope."""

    __tablename__ = "role_binding"
    __table_args__ = (
        CheckConstraint(
            "(scope = 'org' AND project_id IS NULL AND environment_id IS NULL)"
            " OR (scope = 'project' AND project_id IS NOT NULL AND environment_id IS NULL)"
            " OR (scope = 'environment' AND project_id IS NOT NULL"
            " AND environment_id IS NOT NULL)",
            name="scope_matches",
        ),
        UniqueConstraint(
            "subject_kind",
            "subject_id",
            "role_id",
            "scope",
            "project_id",
            "environment_id",
            name="uq_role_binding_subject_role_scope",
            postgresql_nulls_not_distinct=True,
        ),
        Index(None, "subject_kind", "subject_id"),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    subject_kind: Mapped[SubjectKind] = mapped_column(pg_enum(SubjectKind, "subject_kind"))
    subject_id: Mapped[uuid.UUID]
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id", ondelete="RESTRICT"))
    scope: Mapped[ScopeKind] = mapped_column(pg_enum(ScopeKind, "scope_kind"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE")
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE")
    )
    # "manual", or "sso:<provider key>" for bindings managed by SSO.
    source: Mapped[str] = mapped_column(Text, default="manual", server_default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrgSettings(Base):
    """Instance-wide settings: a single row with id 1."""

    __tablename__ = "org_settings"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    require_mfa: Mapped[bool] = mapped_column(default=False, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserSession(Base):
    """A server-side sign-in session. Only the sha256 of the cookie token is stored."""

    __tablename__ = "session"
    __table_args__ = (UniqueConstraint("token_hash"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[SessionState] = mapped_column(pg_enum(SessionState, "session_state"))
    # "password", "sso:<provider key>", ...
    auth_method: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idle_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # When a second factor was last verified in this session (the "recent factor" rule).
    mfa_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))


class ApiToken(Base):
    """A service or personal API token. Only the sha256 of the secret is stored."""

    __tablename__ = "api_token"
    __table_args__ = (
        UniqueConstraint("secret_hash"),
        CheckConstraint(
            "(kind = 'personal') = (owner_user_id IS NOT NULL)", name="personal_has_owner"
        ),
    )
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[ApiTokenKind] = mapped_column(pg_enum(ApiTokenKind, "api_token_kind"))
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(14))
    secret_hash: Mapped[str] = mapped_column(String(64))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    # The user or token that created it; no FK, so the record outlives its creator.
    created_by: Mapped[uuid.UUID | None]
    # Personal tokens only: a subset of the catalogue, optionally limited to a project or env.
    permissions: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE")
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("environment.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL")
    )


class TotpCredential(Base):
    """A user's TOTP secret, encrypted with the SecretBox. Unconfirmed until a code is checked."""

    __tablename__ = "totp_credential"
    __mapper_args__ = {"eager_defaults": True}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    secret_encrypted: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The last 30-second step a code was accepted for; it and earlier steps are refused (replay).
    last_used_step: Mapped[int | None] = mapped_column(BigInteger)


class RecoveryCode(Base):
    """A single-use recovery code. Only the sha256 of the normalised code is stored."""

    __tablename__ = "recovery_code"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebauthnCredential(Base):
    """A passkey registered by a user."""

    __tablename__ = "webauthn_credential"
    __table_args__ = (UniqueConstraint("credential_id"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger)
    transports: Mapped[list[str]] = mapped_column(ARRAY(Text))
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(Base):
    """A one-time link to set a password: an invitation or a reset. Only the sha256 is stored."""

    __tablename__ = "invitation"
    __table_args__ = (UniqueConstraint("token_hash"),)
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[InvitationPurpose] = mapped_column(
        pg_enum(InvitationPurpose, "invitation_purpose")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Set when the link is used, or when a newer link for the same user supersedes it.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
