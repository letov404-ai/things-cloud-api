"""ChecklistItem entity for Things Cloud API (entity type: ChecklistItem3)."""
from __future__ import annotations

from typing import Annotated, Any

import pydantic

from things_cloud.models.todo import EntityType, NewBody, Update, UpdateType
from things_cloud.models.types import ShortUUID, TimestampFloat
from things_cloud.utils import Util


class ChecklistApiObject(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(populate_by_name=True, extra="ignore")

    title: Annotated[str, pydantic.Field(alias="tt")]
    status: Annotated[int, pydantic.Field(alias="ss")] = 0  # 0=todo, 3=complete
    index: Annotated[int, pydantic.Field(alias="ix")] = 0
    creation_date: Annotated[TimestampFloat, pydantic.Field(alias="cd")]
    modification_date: Annotated[TimestampFloat, pydantic.Field(alias="md")]
    task_uuids: Annotated[list[str], pydantic.Field(alias="ts")]  # parent task(s)


class ChecklistItem(pydantic.BaseModel):
    _uuid: ShortUUID = pydantic.PrivateAttr(default_factory=Util.uuid)
    title: str
    task_uuid: str  # parent task UUID
    status: int = 0  # 0=todo, 3=complete
    index: int = 0

    @property
    def uuid(self) -> ShortUUID:
        return self._uuid

    def to_update(self) -> Update:
        api_obj = ChecklistApiObject(
            title=self.title,
            status=self.status,
            index=self.index,
            creation_date=Util.now(),
            modification_date=Util.now(),
            task_uuids=[self.task_uuid],
        )
        body = NewBody(
            payload=api_obj,
            entity=EntityType.CHECKLIST_ITEM_3,
        )
        return Update(id=self._uuid, body=body)

    @classmethod
    def from_api(cls, uuid: ShortUUID, api_obj: ChecklistApiObject) -> ChecklistItem:
        """Create ChecklistItem from API response."""
        task_uuid = api_obj.task_uuids[0] if api_obj.task_uuids else ""
        item = cls(
            title=api_obj.title,
            task_uuid=task_uuid,
            status=api_obj.status,
            index=api_obj.index,
        )
        item._uuid = uuid
        return item
