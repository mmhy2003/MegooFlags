from enum import StrEnum


class FlagType(StrEnum):
    BOOLEAN = "boolean"
    STRING = "string"
    NUMBER = "number"
    JSON = "json"


class Operator(StrEnum):
    IN = "in"
    NOT_IN = "not_in"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    MATCHES = "matches"
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    SEMVER_EQ = "semver_eq"
    SEMVER_LT = "semver_lt"
    SEMVER_GT = "semver_gt"
    BEFORE = "before"
    AFTER = "after"
    IN_SEGMENT = "in_segment"


class SegmentTargetKind(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"


class SdkKeyKind(StrEnum):
    SERVER = "server"
    CLIENT = "client"


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class SubjectKind(StrEnum):
    USER = "user"
    TEAM = "team"
    SERVICE_TOKEN = "service_token"


class ScopeKind(StrEnum):
    ORG = "org"
    PROJECT = "project"
    ENVIRONMENT = "environment"


class SessionState(StrEnum):
    PENDING_MFA = "pending_mfa"  # password checked, second factor still to come
    ENROL_MFA = "enrol_mfa"  # signed in, but the org requires a factor the user has not set up
    ACTIVE = "active"


class ApiTokenKind(StrEnum):
    SERVICE = "service"  # has its own role bindings
    PERSONAL = "personal"  # a permission subset, capped by its owner's rights


class InvitationPurpose(StrEnum):
    INVITE = "invite"  # a new user sets their first password
    RESET = "reset"  # an admin-issued password reset
