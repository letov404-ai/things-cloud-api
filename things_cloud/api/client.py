import httpx
from httpx import HTTPStatusError, Request, RequestError, Response
from structlog import get_logger

from things_cloud.api.account import Account
from things_cloud.api.const import API_BASE, HEADERS
from things_cloud.api.exceptions import ThingsCloudException
from things_cloud.models.tag import TagApiObject, TagItem
from things_cloud.models.todo import (
    CommitResponse,
    Destination,
    EntityType,
    HistoryResponse,
    NewBody,
    Status,
    TodoItem,
    Type,
    Update,
    UpdateType,
)
from things_cloud.utils import Util

log = get_logger()


class ThingsClient:
    def __init__(self, account: Account) -> None:
        self._account = account
        self._items: dict[str, TodoItem] = {}
        self._tags: dict[str, TagItem] = {}
        self._base_url: str = f"{API_BASE}/history/{account._info.history_key}"
        self._client = httpx.Client(
            base_url=self._base_url,
            headers=HEADERS,
            event_hooks={
                "request": [self.log_request],
                "response": [self.log_response, self.raise_on_4xx_5xx],
            },
        )
        self._session = account.new_session()
        self._offset = self._session.head_index

    def __del__(self):
        self._client.close()

    @staticmethod
    def log_request(request: Request) -> None:
        log.debug(f"Request: {request.method} {request.url} - Waiting for response")

    @staticmethod
    def raise_on_4xx_5xx(response: Response) -> None:
        """Raises a HTTPStatusError on 4xx and 5xx responses."""
        try:
            response.raise_for_status()
        except HTTPStatusError as err:
            raise ThingsCloudException from err

    @staticmethod
    def log_response(response: Response) -> None:
        request = response.request
        log.debug(
            f"Response: {request.method} {request.url}", status=response.status_code
        )
        response.read()  # access response body
        log.debug("Body", content=response.content)

    def update(self) -> None:
        data = self.__fetch(self._offset)
        self._process_history(data)
        self._offset = data.current_item_index

    def commit(self, item: TodoItem) -> None:
        update = item.to_update()
        try:
            commit = self.__commit(update)
            item._commit(update.body.payload)
            self._offset = commit.server_head_index
        except ThingsCloudException as e:
            log.error("Error commiting")
            raise e

    def __request(self, method: str, endpoint: str, **kwargs) -> Response:
        try:
            return self._client.request(method, endpoint, **kwargs)
        except RequestError as e:
            raise ThingsCloudException from e

    def __fetch(self, index: int) -> HistoryResponse:
        response = self.__request(
            "GET",
            "/items",
            params={
                "start-index": str(index),
            },
        )
        if response.status_code == 200:
            return HistoryResponse.model_validate_json(response.read())
        else:
            log.error("Error getting current index", response=response)
            raise ThingsCloudException

    def _process_history(self, history: HistoryResponse) -> None:
        for update in history.updates:
            log.debug("processing update", update=update)
            match update.body.type:
                case UpdateType.NEW:
                    assert isinstance(
                        update.body, NewBody
                    )  # HACK: type narrowing does not work
                    entity = str(update.body.entity)
                    if entity == EntityType.TAG_3:
                        payload = update.body.payload
                        if isinstance(payload, dict):
                            api_obj = TagApiObject.model_validate(payload)
                        else:
                            api_obj = payload
                        tag = TagItem.from_api(update.id, api_obj)
                        self._tags[tag.uuid] = tag
                    else:
                        item = update.body.payload.to_todo()
                        item._uuid = update.id
                        self._items[item.uuid] = item
                case UpdateType.EDIT:
                    try:
                        item = self._items[update.id]
                    except KeyError as key_err:
                        msg = f"todo {id} not found"
                        raise ValueError(msg) from key_err
                    update.body.payload.apply_edits(item)

    def _active_tasks(self) -> list[TodoItem]:
        """All non-trashed, non-completed tasks of type TASK."""
        return [
            item for item in self._items.values()
            if not item.trashed and item.status == Status.TODO and item.type == Type.TASK
        ]

    def inbox(self) -> list[TodoItem]:
        """Tasks in Inbox (not trashed, not completed)."""
        return [
            item for item in self._active_tasks()
            if item.destination == Destination.INBOX
        ]

    def today(self) -> list[TodoItem]:
        """Tasks scheduled for today."""
        return [
            item for item in self._active_tasks()
            if item.is_today
        ]

    def anytime(self) -> list[TodoItem]:
        """Tasks with destination=ANYTIME, excluding today's scheduled."""
        return [
            item for item in self._active_tasks()
            if item.destination == Destination.ANYTIME and not item.is_today
        ]

    def someday(self) -> list[TodoItem]:
        """Tasks with destination=SOMEDAY."""
        return [
            item for item in self._active_tasks()
            if item.destination == Destination.SOMEDAY
        ]

    def projects(self) -> list[TodoItem]:
        """All non-trashed project items."""
        return [
            item for item in self._items.values()
            if not item.trashed and item.type == Type.PROJECT
        ]

    def by_project(self, project_uuid: str) -> list[TodoItem]:
        """Tasks belonging to a specific project."""
        return [
            item for item in self._active_tasks()
            if item.project == project_uuid
        ]

    def completed(self) -> list[TodoItem]:
        """All completed items (not trashed)."""
        return [
            item for item in self._items.values()
            if item.status == Status.COMPLETE and not item.trashed
        ]

    def trashed(self) -> list[TodoItem]:
        """All trashed items."""
        return [item for item in self._items.values() if item.trashed]

    def get(self, uuid: str) -> TodoItem | None:
        """Get a specific item by UUID."""
        return self._items.get(uuid)

    def all_tasks(self) -> list[TodoItem]:
        """All non-trashed tasks (any status)."""
        return [
            item for item in self._items.values()
            if not item.trashed and item.type == Type.TASK
        ]

    def tags(self) -> list[TagItem]:
        """All tags."""
        return list(self._tags.values())

    def __commit(self, update: Update) -> CommitResponse:
        response = self.__request(
            method="POST",
            endpoint="/commit",
            params={
                "ancestor-index": str(self._offset),
                "_cnt": "1",
            },
            json=update.to_api_payload(),
        )
        return CommitResponse.model_validate_json(response.read())
