"""All models, imported so that `Base.metadata` is complete."""

from megooflags_api.models.flag import Flag, Variation
from megooflags_api.models.identity import (
    OrgSettings,
    Role,
    RoleBinding,
    Team,
    TeamMember,
    User,
    UserSession,
)
from megooflags_api.models.project import Environment, Project
from megooflags_api.models.sdk import EvaluationCount, ExposureEvent, SdkKey
from megooflags_api.models.segment import Segment, SegmentRule, SegmentTarget
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
    "OrgSettings",
    "Project",
    "Role",
    "RoleBinding",
    "RolloutWeight",
    "SdkKey",
    "Segment",
    "SegmentRule",
    "SegmentTarget",
    "Team",
    "TeamMember",
    "User",
    "UserSession",
    "Variation",
]
