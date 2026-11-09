"""All models, imported so that `Base.metadata` is complete."""

from megooflags_api.models.flag import Flag, Variation
from megooflags_api.models.project import Environment, Project
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
    "Flag",
    "FlagEnvironment",
    "FlagPrerequisite",
    "FlagRule",
    "FlagTarget",
    "Project",
    "RolloutWeight",
    "Segment",
    "SegmentRule",
    "SegmentTarget",
    "Variation",
]
