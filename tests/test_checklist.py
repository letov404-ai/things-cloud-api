"""Tests for ChecklistItem3 entity support."""
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from pytest_httpx import HTTPXMock

from things_cloud.api.account import Account, Credentials
from things_cloud.api.client import ThingsClient
from things_cloud.models.checklist import ChecklistItem
from things_cloud.models.todo import HistoryResponse
from things_cloud.utils import Util

FAKE_TIME = datetime(2024, 12, 9, 12, 0, 0, tzinfo=UTC)
PARENT_TASK_UUID = "parentTaskUuid22chars0"


@pytest.fixture(autouse=True)
def mock_time(monkeypatch):
    monkeypatch.setattr("things_cloud.utils.Util.now", lambda: FAKE_TIME)


# --- ChecklistItem model ---

def test_checklist_creation():
    item = ChecklistItem(title="Buy milk", task_uuid=PARENT_TASK_UUID)
    assert item.title == "Buy milk"
    assert item.task_uuid == PARENT_TASK_UUID
    assert len(item.uuid) == 22


def test_checklist_status_default():
    item = ChecklistItem(title="Test", task_uuid=PARENT_TASK_UUID)
    assert item.status == 0  # todo


def test_checklist_complete():
    item = ChecklistItem(title="Test", task_uuid=PARENT_TASK_UUID)
    item.status = 3  # complete
    assert item.status == 3


def test_checklist_to_update():
    item = ChecklistItem(title="Buy milk", task_uuid=PARENT_TASK_UUID)
    update = item.to_update()
    payload = update.to_api_payload()
    uuid_key = list(payload.keys())[0]
    body = payload[uuid_key]
    assert body["e"] == "ChecklistItem3"
    assert body["t"] == 0  # NEW
    assert body["p"]["tt"] == "Buy milk"
    assert body["p"]["ts"] == [PARENT_TASK_UUID]


# --- ThingsClient checklist storage ---

@pytest.fixture()
def account_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture()
def account(account_id: uuid.UUID, httpx_mock: HTTPXMock) -> Account:
    credentials = Credentials(
        email="test@example.com", password=SecretStr("pass123")
    )
    httpx_mock.add_response(
        200,
        json={
            "SLA-version-accepted": "5",
            "email": "test@example.com",
            "history-key": str(account_id),
            "issues": [],
            "maildrop-email": "test@things.email",
            "status": "SYAccountStatusActive",
        },
    )
    return Account.login(credentials)


@pytest.fixture()
def things(account: Account, httpx_mock: HTTPXMock) -> ThingsClient:
    httpx_mock.reset()
    httpx_mock.add_response(
        201,
        json={"headIndex": 0, "historyKeySessionSecret": "fake"},
    )
    return ThingsClient(account)


def test_checklists_for_empty(things: ThingsClient):
    assert things.checklists_for(PARENT_TASK_UUID) == []


def test_process_history_checklist_new(things: ThingsClient):
    """Processing ChecklistItem3 NEW history stores checklist item."""
    cl_uuid = Util.uuid()
    history_data = {
        "items": [
            {
                cl_uuid: {
                    "p": {
                        "tt": "Buy eggs",
                        "ss": 0,
                        "ix": 0,
                        "cd": 1733747506.0,
                        "md": 1733747506.0,
                        "ts": [PARENT_TASK_UUID],
                    },
                    "e": "ChecklistItem3",
                    "t": 0,
                }
            }
        ],
        "current-item-index": 1,
        "schema": 301,
        "start-total-content-size": 1,
        "end-total-content-size": 100,
        "latest-total-content-size": 100,
    }
    history = HistoryResponse.model_validate(history_data)
    things._process_history(history)

    items = things.checklists_for(PARENT_TASK_UUID)
    assert len(items) == 1
    assert items[0].title == "Buy eggs"
    assert items[0].task_uuid == PARENT_TASK_UUID


def test_checklists_for_filters_by_task(things: ThingsClient):
    """checklists_for only returns items for the specified task."""
    other_task = "otherTaskUuid22chars00"
    cl1 = ChecklistItem(title="Item 1", task_uuid=PARENT_TASK_UUID)
    cl2 = ChecklistItem(title="Item 2", task_uuid=other_task)
    things._checklist_items[cl1.uuid] = cl1
    things._checklist_items[cl2.uuid] = cl2

    result = things.checklists_for(PARENT_TASK_UUID)
    assert len(result) == 1
    assert result[0].title == "Item 1"
