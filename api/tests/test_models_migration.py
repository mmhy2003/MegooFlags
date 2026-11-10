import megooflags_api.models  # noqa: F401
from megooflags_api.db import Base
from megooflags_api.models.enums import FlagType, Operator, SdkKeyKind


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
