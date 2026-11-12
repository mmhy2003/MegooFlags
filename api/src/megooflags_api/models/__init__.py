"""All models, imported so that `Base.metadata` is complete."""

from megooflags_api.models.audit import AuditEntry
from megooflags_api.models.flag import Flag, Variation
from megooflags_api.models.identity import (
    ApiToken,
    Invitation,
    OrgSettings,
    RecoveryCode,
    Role,
    RoleBinding,
    Team,
    TeamMember,
    TotpCredential,
    User,
    UserSession,
    WebauthnCredential,
)
from megooflags_api.models.project import Environment, Project
from megooflags_api.models.release import (
    ChangeRequest,
    ChangeRequestComment,
    ChangeRequestReview,
    ProgressiveRollout,
    ScheduledChange,
)
from megooflags_api.models.sdk import EvaluationCount, ExposureEvent, SdkKey
from megooflags_api.models.segment import Segment, SegmentRule, SegmentTarget
from megooflags_api.models.sso import SsoGroupMapping, SsoProvider, UserIdentity
from megooflags_api.models.targeting import (
    Clause,
    ClauseSegment,
    FlagEnvironment,
    FlagPrerequisite,
    FlagRule,
    FlagTarget,
    RolloutWeight,
)

__all__ = [
    "ApiToken",
    "AuditEntry",
    "ChangeRequest",
    "ChangeRequestComment",
    "ChangeRequestReview",
    "Clause",
    "ClauseSegment",
    "Environment",
    "EvaluationCount",
    "ExposureEvent",
    "Flag",
    "FlagEnvironment",
    "FlagPrerequisite",
    "FlagRule",
    "FlagTarget",
    "Invitation",
    "OrgSettings",
    "ProgressiveRollout",
    "Project",
    "RecoveryCode",
    "Role",
    "RoleBinding",
    "RolloutWeight",
    "ScheduledChange",
    "SdkKey",
    "Segment",
    "SegmentRule",
    "SegmentTarget",
    "SsoGroupMapping",
    "SsoProvider",
    "Team",
    "TeamMember",
    "TotpCredential",
    "User",
    "UserIdentity",
    "UserSession",
    "Variation",
    "WebauthnCredential",
]
