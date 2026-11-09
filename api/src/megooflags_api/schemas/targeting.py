from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr

from megooflags_api.models.enums import Operator
from megooflags_api.schemas.common import ApiModel

Scalar = StrictStr | StrictInt | StrictFloat | StrictBool


class ClauseIn(ApiModel):
    attribute: str = Field("key", min_length=1, max_length=200)
    operator: Operator
    values: list[Scalar] = Field(min_length=1, max_length=1000)
    negate: bool = False


class ClauseOut(ApiModel):
    """For `in_segment`, `values` are the segment keys, sorted."""

    attribute: str
    operator: Operator
    values: list[Scalar]
    negate: bool
