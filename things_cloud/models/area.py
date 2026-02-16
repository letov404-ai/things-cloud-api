"""Area entity for Things Cloud API (entity type: Area2)."""
from __future__ import annotations

from typing import Annotated, Any

import pydantic

from things_cloud.models.todo import EntityType, NewBody, Update, UpdateType
from things_cloud.models.types import ShortUUID
from things_cloud.utils import Util


class AreaApiObject(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(populate_by_name=True)

    title: Annotated[str, pydantic.Field(alias="tt")]
    index: Annotated[int, pydantic.Field(alias="ix")] = 0
    tags: Annotated[list[str], pydantic.Field(alias="tg")] = []


class AreaItem(pydantic.BaseModel):
    _uuid: ShortUUID = pydantic.PrivateAttr(default_factory=Util.uuid)
    title: str
    tags: list[str] = []

    @property
    def uuid(self) -> ShortUUID:
        return self._uuid

    def to_update(self) -> Update:
        api_obj = AreaApiObject(
            title=self.title,
            tags=self.tags,
        )
        body = NewBody(
            payload=api_obj,
            entity=EntityType.AREA_2,
        )
        return Update(id=self._uuid, body=body)

    @classmethod
    def from_api(cls, uuid: ShortUUID, api_obj: AreaApiObject) -> AreaItem:
        """Create AreaItem from API response."""
        area = cls(
            title=api_obj.title,
            tags=api_obj.tags,
        )
        area._uuid = uuid
        return area
