from megooflags_api.schemas.common import ApiModel


class UpdateOut(ApiModel):
    version: str
    url: str


class SystemVersionOut(ApiModel):
    version: str
    update: UpdateOut | None
