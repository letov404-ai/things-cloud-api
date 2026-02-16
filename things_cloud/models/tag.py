"""Tag entity for Things Cloud API (entity type: Tag3)."""
from __future__ import annotations

from typing import Annotated, Any

import pydantic

from things_cloud.models.todo import EntityType, NewBody, Update, UpdateType
from things_cloud.models.types import ShortUUID
from things_cloud.utils import Util


class TagApiObject(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(populate_by_name=True, extra="ignore")

    title: Annotated[str, pydantic.Field(alias="tt")]
    parent: Annotated[list[str] | str | None, pydantic.Field(alias="pn")] = None
    short_name: Annotated[str, pydantic.Field(alias="sn")] = ""
    index: Annotated[int, pydantic.Field(alias="ix")] = 0


class TagItem(pydantic.BaseModel):
    _uuid: ShortUUID = pydantic.PrivateAttr(default_factory=Util.uuid)
    title: str
    parent: str | None = None
    short_name: str = ""

    @property
    def uuid(self) -> ShortUUID:
        return self._uuid

    def to_update(self) -> Update:
        api_obj = TagApiObject(
            title=self.title,
            parent=self.parent,
            short_name=self.short_name,
        )
        body = NewBody(
            payload=api_obj,
            entity=EntityType.TAG_3,
        )
        return Update(id=self._uuid, body=body)

    @classmethod
    def from_api(cls, uuid: ShortUUID, api_obj: TagApiObject) -> TagItem:
        """Create TagItem from API response."""
        parent = api_obj.parent
        if isinstance(parent, list):
            parent = parent[0] if parent else None
        tag = cls(
            title=api_obj.title,
            parent=parent,
            short_name=api_obj.short_name,
        )
        tag._uuid = uuid
        return tag
