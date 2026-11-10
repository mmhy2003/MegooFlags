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
