import megooflags_api.models  # noqa: F401
from megooflags_api.db import Base
from megooflags_api.models.enums import (
    ApiTokenKind,
    FlagType,
    InvitationPurpose,
    Operator,
    ScopeKind,
    SdkKeyKind,
    SessionState,
    SsoProtocol,
    SubjectKind,
    UserStatus,
)


def test_metadata_has_all_tables() -> None:
    assert set(Base.metadata.tables) == {
        "project",
        "environment",
        "flag",
        "variation",
        "flag_environment",
        "flag_target",
        "flag_rule",
        "rollout_weight",
        "clause",
        "clause_segment",
        "flag_prerequisite",
        "segment",
        "segment_target",
        "segment_rule",
        "sdk_key",
        "exposure_event",
        "evaluation_count",
        "user",
        "team",
        "team_member",
        "role",
        "role_binding",
        "org_settings",
        "session",
        "api_token",
        "audit_entry",
        "invitation",
        "totp_credential",
        "recovery_code",
        "webauthn_credential",
        "sso_provider",
        "sso_group_mapping",
        "user_identity",
    }


def test_enum_values() -> None:
    assert [t.value for t in FlagType] == ["boolean", "string", "number", "json"]
    assert [o.value for o in Operator] == [
        "in",
        "not_in",
        "contains",
        "starts_with",
        "ends_with",
        "matches",
        "lt",
        "lte",
        "gt",
        "gte",
        "semver_eq",
        "semver_lt",
        "semver_gt",
        "before",
        "after",
        "in_segment",
    ]
    assert [k.value for k in SdkKeyKind] == ["server", "client"]
    assert [s.value for s in UserStatus] == ["active", "disabled"]
    assert [k.value for k in SubjectKind] == ["user", "team", "service_token"]
    assert [s.value for s in ScopeKind] == ["org", "project", "environment"]
    assert [s.value for s in SessionState] == ["pending_mfa", "enrol_mfa", "active"]
    assert [k.value for k in ApiTokenKind] == ["service", "personal"]
    assert [p.value for p in InvitationPurpose] == ["invite", "reset"]
    assert [p.value for p in SsoProtocol] == ["oidc", "saml"]
