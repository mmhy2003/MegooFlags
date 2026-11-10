from typing import Any

import pytest

from megooflags_api.errors import ApiError
from megooflags_api.models.enums import FlagType
from megooflags_api.services.values import check_value


def assert_rejected(flag_type: FlagType, value: Any) -> None:
    with pytest.raises(ApiError) as info:
        check_value(flag_type, value, "variations[0].value")
    assert info.value.status == 422
    assert info.value.code == "validation_error"
    assert info.value.details == {"path": "variations[0].value"}


def test_string() -> None:
    check_value(FlagType.STRING, "x", "v")
    assert_rejected(FlagType.STRING, 1)
    assert_rejected(FlagType.STRING, True)


def test_number() -> None:
    check_value(FlagType.NUMBER, 1, "v")
    check_value(FlagType.NUMBER, 1.5, "v")
    for bad in (True, "1", float("nan"), float("inf")):
        assert_rejected(FlagType.NUMBER, bad)


def test_boolean() -> None:
    check_value(FlagType.BOOLEAN, True, "v")
    check_value(FlagType.BOOLEAN, False, "v")
    for bad in (1, 0, "true", None):
        assert_rejected(FlagType.BOOLEAN, bad)


@pytest.mark.parametrize("value", [{"a": [1]}, [1, "x"], "s", 2, 2.5, False, None])
def test_json_accepts_any_json(value: Any) -> None:
    check_value(FlagType.JSON, value, "v")
